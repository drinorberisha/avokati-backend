from typing import List, Optional
from sqlalchemy.orm import Session
from app.db.models.client import Client
from app.schemas.client import ClientCreate, ClientUpdate

def get_client(db: Session, client_id: str) -> Optional[Client]:
    return db.query(Client).filter(Client.id == client_id).first()

def get_client_by_email(db: Session, email: str) -> Optional[Client]:
    return db.query(Client).filter(Client.email == email).first()

def get_clients(db: Session, skip: int = 0, limit: int = 100) -> List[Client]:
    return db.query(Client).offset(skip).limit(limit).all()

def create_client(db: Session, client: ClientCreate) -> Client:
    db_client = Client(
        name=client.name,
        email=client.email,
        phone=client.phone,
        status=client.status,
        address=client.address
    )
    db.add(db_client)
    db.commit()
    db.refresh(db_client)
    return db_client

def update_client(db: Session, client: Client, client_in: ClientUpdate) -> Client:
    update_data = client_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(client, field, value)
    db.commit()
    db.refresh(client)
    return client

def delete_client(db: Session, client_id: str) -> Client:
    client = get_client(db, client_id=client_id)
    db.delete(client)
    db.commit()
    return client 