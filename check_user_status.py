import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import os
from dotenv import load_dotenv

load_dotenv()

async def check_user():
    mongo_uri = os.getenv("MONGO_URI") or "mongodb://127.0.0.1:27017/iot_auth_db"
    client = AsyncIOMotorClient(mongo_uri)
    db = client.get_default_database()
    
    user = await db.users.find_one({"username": "krishna99"})
    if user:
        print(f"User found: {user.get('username')}")
        print(f"is_active: {user.get('is_active')}")
        print(f"email: {user.get('email')}")
        
        if user.get('is_active') is False:
            print("Fixing user: setting is_active to True...")
            await db.users.update_one({"_id": user["_id"]}, {"$set": {"is_active": True}})
            print("Update successful.")
        else:
            print("User is already active or is_active field is missing (defaults to True).")
    else:
        print("User 'krishna99' not found in database.")

if __name__ == "__main__":
    asyncio.run(check_user())
