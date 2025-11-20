import strawberry
from strawberry.fastapi import GraphQLRouter
from typing import List, Optional

# Example user type
@strawberry.type
class User:
    id: strawberry.ID
    username: str
    email: str

# Example device type (expand as needed)
@strawberry.type
class Device:
    id: strawberry.ID
    name: str
    status: Optional[str] = None

# Queries
@strawberry.type
class Query:
    @strawberry.field
    def users(self) -> List[User]:
        # Placeholder static list, replace with db lookup
        return [User(id="1", username="john", email="john@example.com")]

    @strawberry.field
    def devices(self) -> List[Device]:
        # Placeholder static list, replace with db lookup
        return [Device(id="1", name="Sensor", status="online")]

# Example Mutation
@strawberry.type
class Mutation:
    @strawberry.field
    def create_user(self, username: str, email: str) -> User:
        # Add actual db insert here
        return User(id="2", username=username, email=email)

schema = strawberry.Schema(query=Query, mutation=Mutation)

graphql_app = GraphQLRouter(schema)
