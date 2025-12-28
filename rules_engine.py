import json
import os
import logging
import re
import time
from typing import Any, Dict, Optional
from bson import ObjectId
from db import db, doc_to_dict

logger = logging.getLogger(__name__)

class RulesEngine:
    def __init__(self, rules_file: str = "security_rules.json", cache_ttl: int = 60):
        self.rules = {}
        self.rules_file = rules_file
        self.cache_ttl = cache_ttl
        self.last_check_time = 0
        self.last_mtime = 0
        self.compiled_rules = {}
        self.load_rules(force=True)

    def load_rules(self, force=False):
        now = time.time()
        # Cache check: Only check file system if TTL expired
        if not force and (now - self.last_check_time < self.cache_ttl):
            return

        self.last_check_time = now
        try:
            path = os.path.join(os.path.dirname(__file__), self.rules_file)
            if os.path.exists(path):
                mtime = os.path.getmtime(path)
                if mtime > self.last_mtime or force:
                    with open(path, 'r') as f:
                        self.rules = json.load(f)
                    self.last_mtime = mtime
                    self.compiled_rules.clear() # Clear compiled cache on reload
                    logger.info(f"✅ Loaded security rules from {self.rules_file}")
            else:
                logger.warning(f"⚠️ Security rules file not found at {path}")
        except Exception as e:
            logger.error(f"❌ Failed to load security rules: {e}")
            self.rules = {}

    def _get_rule_string(self, collection: str, operation: str) -> Optional[str]:
        """
        Retrieves the rule string for a given collection and operation (.read/.write).
        Matches structure: collection -> $wildcard -> operation
        """
        node = self.rules.get(collection)
        if not node:
            return None
            
        # Check for wildcard child (e.g., $device_id)
        for key in node:
            if key.startswith("$"):
                child = node[key]
                if operation in child:
                    return child[operation]
        
        # Check direct operation (less common in this schema but possible)
        if operation in node:
            return node[operation]
            
        return None

    class MockObject(dict):
        """Helper to allow dot notation access in eval context (e.g. auth.uid)"""
        def __getattr__(self, item):
            return self.get(item)

    async def validate_rule(self, collection_name: str, operation: str, user_id: Any, resource_data: Dict[str, Any], extra_context: Dict[str, Any] = None) -> bool:
        """
        Evaluates the security rule for the given context.
        """
        # Ensure rules are up to date (throttled check)
        self.load_rules()

        rule_expr = self._get_rule_string(collection_name, operation)
        
        if not rule_expr:
            # If no rule is defined, default to DENY for security
            logger.warning(f"⛔ No rule found for {collection_name} {operation}. Access Denied.")
            return False

        # Prepare Context
        # Convert ObjectIds to strings for comparison in rules
        user_id_str = str(user_id) if user_id else None
        
        # Normalize resource data (ensure IDs are strings)
        data_dict = self.MockObject(doc_to_dict(resource_data)) if resource_data else self.MockObject({})
        
        context = {
            "auth": self.MockObject({"uid": user_id_str}),
            "data": data_dict,
            "root": self.MockObject({})
        }

        if extra_context:
            context.update(extra_context)

        # 🔍 Handle 'root' lookups (e.g., root.dashboards[data.dashboard_id])
        # Regex to find patterns like: root.dashboards[data.dashboard_id] or nested root.widgets[data.widget_id].dashboard_id
        # We support patterns: root.collection_name[data.field_name] and nested access
        root_matches = re.findall(r"root\.(\w+)\[([^\]]+)\]", rule_expr)
        
        # Create a custom RootAccess class for dynamic lookups
        class RootAccess:
            def __init__(self, context_dict):
                self._data = context_dict
                
            def __getattr__(self, collection_name):
                collection_data = self._data.get(collection_name, {})
                return CollectionAccess(collection_name, collection_data, data_dict)
        
        class CollectionAccess:
            def __init__(self, collection_name, collection_data, data_dict_ref):
                self._collection_name = collection_name
                self._collection_data = collection_data
                self._data_dict = data_dict_ref
                
            def __getitem__(self, key):
                # Handle nested access like root.widgets[data.widget_id].dashboard_id
                if isinstance(key, str) and key.startswith("data."):
                    # Extract field name from data.field_name
                    field_name = key.replace("data.", "")
                    ref_id = self._data_dict.get(field_name)
                    if ref_id:
                        return self._get_document(ref_id)
                elif isinstance(key, str) and key.startswith("root."):
                    # Handle nested root access: root.widgets[data.widget_id].dashboard_id
                    # This will be handled by the outer RootAccess
                    return None
                elif isinstance(key, str):
                    # Direct ID access
                    return self._get_document(key)
                return None
                
            def _get_document(self, doc_id):
                if doc_id in self._collection_data:
                    return self._collection_data[doc_id]
                return None
        
        # Fetch all referenced documents for root lookups
        fetched_collections = set()
        root_data = {}
        
        for col_name, key_expr in root_matches:
            if col_name not in fetched_collections:
                # Extract field name if it's data.field_name
                if key_expr.startswith("data."):
                    field_name = key_expr.replace("data.", "")
                    ref_id = data_dict.get(field_name)
                else:
                    ref_id = key_expr
                
                if ref_id:
                    collection = getattr(db, col_name, None)
                    if collection:
                        try:
                            ref_doc = await collection.find_one({"_id": ObjectId(ref_id)})
                            if ref_doc:
                                if col_name not in root_data:
                                    root_data[col_name] = {}
                                root_data[col_name][ref_id] = self.MockObject(doc_to_dict(ref_doc))
                        except Exception as e:
                            logger.debug(f"Could not fetch {col_name}[{ref_id}]: {e}")
                fetched_collections.add(col_name)
        
        # Handle nested root lookups (e.g., root.dashboards[root.widgets[data.widget_id].dashboard_id])
        # This requires a second pass after initial documents are fetched
        nested_matches = re.findall(r"root\.(\w+)\[root\.(\w+)\[([^\]]+)\]\.(\w+)\]", rule_expr)
        for target_col, source_col, source_key, source_field in nested_matches:
            # Get source document
            source_ref_id = None
            if source_key.startswith("data."):
                source_field_name = source_key.replace("data.", "")
                source_ref_id = data_dict.get(source_field_name)
            else:
                source_ref_id = source_key
            
            if source_ref_id and source_col in root_data:
                source_doc = root_data[source_col].get(source_ref_id)
                if source_doc:
                    nested_ref_id = getattr(source_doc, source_field, None) or source_doc.get(source_field)
                    if nested_ref_id:
                        collection = getattr(db, target_col, None)
                        if collection:
                            try:
                                ref_doc = await collection.find_one({"_id": ObjectId(nested_ref_id)})
                                if ref_doc:
                                    if target_col not in root_data:
                                        root_data[target_col] = {}
                                    root_data[target_col][nested_ref_id] = self.MockObject(doc_to_dict(ref_doc))
                            except Exception as e:
                                logger.debug(f"Could not fetch nested {target_col}[{nested_ref_id}]: {e}")
        
        # Replace context["root"] with RootAccess for dynamic lookups
        context["root"] = RootAccess(root_data)

        try:
            # Evaluate
            # We use a restricted scope (empty __builtins__) for safety
            # Support both == (Python) and === (JS-like) for compatibility
            rule_expr_python = rule_expr.replace("===", "==")
            
            # Cache compiled code objects to avoid re-parsing
            cache_key = rule_expr_python
            if cache_key not in self.compiled_rules:
                self.compiled_rules[cache_key] = compile(rule_expr_python, '<string>', 'eval')
            
            result = eval(self.compiled_rules[cache_key], {"__builtins__": {}}, context)
            return bool(result)
        except Exception as e:
            logger.error(f"❌ Rule evaluation error: {e} | Rule: {rule_expr} | Context keys: {list(context.keys())}")
            return False

rules_engine = RulesEngine()