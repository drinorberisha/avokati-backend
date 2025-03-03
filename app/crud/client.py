from typing import List, Optional, Dict, Any, Union
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
import logging
from app.db.models.client import Client
from app.schemas.client import ClientCreate, ClientUpdate

logger = logging.getLogger(__name__)

async def get_client(db: AsyncSession, client_id: str) -> Optional[Client]:
    try:
        result = await db.execute(select(Client).where(Client.id == client_id))
        return result.scalar_one_or_none()
    except SQLAlchemyError as e:
        logger.error(f"Database error in get_client: {e}")
        return None

async def get_client_by_email(db: AsyncSession, email: str) -> Optional[Client]:
    try:
        result = await db.execute(select(Client).where(Client.email == email))
        return result.scalar_one_or_none()
    except SQLAlchemyError as e:
        logger.error(f"Database error in get_client_by_email: {e}")
        return None

async def get_clients(db: AsyncSession, skip: int = 0, limit: int = 100) -> List[Client]:
    try:
        result = await db.execute(select(Client).offset(skip).limit(limit))
        return result.scalars().all()
    except SQLAlchemyError as e:
        logger.error(f"Database error in get_clients: {e}")
        return []

async def create_client(db: AsyncSession, client: ClientCreate) -> Optional[Client]:
    try:
        db_client = Client(
            name=client.name,
            email=client.email,
            phone=client.phone,
            status=client.status,
            address=client.address
        )
        db.add(db_client)
        await db.commit()
        await db.refresh(db_client)
        return db_client
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error in create_client: {e}")
        return None

async def update_client(db: AsyncSession, client_id: str, client_in: Union[ClientUpdate, Dict[str, Any]]) -> Optional[Client]:
    try:
        db_client = await get_client(db, client_id)
        if not db_client:
            return None
            
        if isinstance(client_in, dict):
            update_data = client_in
        else:
            update_data = client_in.model_dump(exclude_unset=True)
            
        for field, value in update_data.items():
            setattr(db_client, field, value)
            
        await db.commit()
        await db.refresh(db_client)
        return db_client
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error in update_client: {e}")
        return None

async def delete_client(db: AsyncSession, client_id: str) -> bool:
    try:
        db_client = await get_client(db, client_id)
        if not db_client:
            return False
            
        await db.delete(db_client)
        await db.commit()
        return True
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error in delete_client: {e}")
        return False 