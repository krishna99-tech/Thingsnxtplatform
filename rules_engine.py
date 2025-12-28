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
        # Regex to find patterns like: root.dashboards[data.dashboard_id]
        # We support a specific pattern: root.collection_name[data.field_name]
        root_matches = re.findall(r"root\.(\w+)\[data\.(\w+)\]", rule_expr)
        
        for col_name, field_name in root_matches:
            ref_id = data_dict.get(field_name)
            if ref_id:
                # Fetch the referenced document
                # Note: This assumes db collection names match rule collection names
                collection = getattr(db, col_name, None)
                if collection:
                    ref_doc = await collection.find_one({"_id": ObjectId(ref_id)})
                    if ref_doc:
                        # Add to context under root.collection_name[ref_id]
                        # But since we can't easily map dynamic dict access in eval without a custom class,
                        # we will simplify the rule evaluation by replacing the string expression 
                        # or by populating a specific structure.
                        
                        # Strategy: Create a nested dict structure for the specific lookup
                        if col_name not in context["root"]:
                            context["root"][col_name] = {}
                        
                        context["root"][col_name][ref_id] = self.MockObject(doc_to_dict(ref_doc))

        try:
            # Evaluate
            # We use a restricted scope (empty __builtins__) for safety
            # Replace JS-like syntax if present (though we use Python syntax in JSON)
            
            # Cache compiled code objects to avoid re-parsing
            if rule_expr not in self.compiled_rules:
                self.compiled_rules[rule_expr] = compile(rule_expr, '<string>', 'eval')
            
            result = eval(self.compiled_rules[rule_expr], {"__builtins__": {}}, context)
            return bool(result)
        except Exception as e:
            logger.error(f"❌ Rule evaluation error: {e} | Rule: {rule_expr}")
            return False

rules_engine = RulesEngine()