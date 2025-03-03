from typing import List, Optional, Union, Dict, Any
from uuid import UUID
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import SQLAlchemyError
import logging
from app.db.models import Case, CaseStatus as DBCaseStatus
from app.schemas.case import CaseCreate, CaseUpdate

logger = logging.getLogger(__name__)

async def get_case(db: AsyncSession, case_id: str) -> Optional[Case]:
    """
    Get a case by ID with client relationship loaded.
    """
    try:
        result = await db.execute(
            select(Case)
            .options(joinedload(Case.client), joinedload(Case.primary_attorney), joinedload(Case.documents))
            .where(Case.id == case_id)
        )
        return result.unique().scalar_one_or_none()
    except SQLAlchemyError as e:
        logger.error(f"Database error in get_case: {e}")
        return None

async def get_case_by_number(db: AsyncSession, case_number: str) -> Optional[Case]:
    """
    Get a case by case number with client relationship loaded.
    """
    try:
        result = await db.execute(
            select(Case)
            .options(joinedload(Case.client), joinedload(Case.primary_attorney), joinedload(Case.documents))
            .where(Case.case_number == case_number)
        )
        return result.unique().scalar_one_or_none()
    except SQLAlchemyError as e:
        logger.error(f"Database error in get_case_by_number: {e}")
        return None

async def get_cases(
    db: AsyncSession, 
    skip: int = 0, 
    limit: int = 100,
    filters: Optional[Dict[str, Any]] = None
) -> List[Case]:
    """
    Get a list of cases with optional filtering.
    """
    try:
        query = select(Case).options(
            joinedload(Case.client), 
            joinedload(Case.primary_attorney), 
            joinedload(Case.documents)
        )
        
        # Apply filters if provided
        if filters:
            if client_id := filters.get("client_id"):
                query = query.where(Case.client_id == client_id)
            if attorney_id := filters.get("attorney_id"):
                query = query.where(Case.primary_attorney_id == attorney_id)
            if status := filters.get("status"):
                query = query.where(Case.status == status)
        
        # Apply pagination
        query = query.offset(skip).limit(limit)
        
        result = await db.execute(query)
        return list(result.unique().scalars().all())
    except SQLAlchemyError as e:
        logger.error(f"Database error in get_cases: {e}")
        return []

async def get_client_cases(db: AsyncSession, client_id: str) -> List[Case]:
    """
    Get all cases for a specific client.
    """
    return await get_cases(db, filters={"client_id": client_id})

async def create_case(db: AsyncSession, case: CaseCreate) -> Optional[Case]:
    """
    Create a new case with auto-generated case number.
    """
    logger.info("Starting case creation in CRUD layer")
    
    try:
        # Generate a unique case number
        result = await db.execute(select(Case).order_by(Case.case_number.desc()))
        latest_case = result.scalar_one_or_none()
        
        if latest_case:
            last_number = int(latest_case.case_number.split('-')[1])
            new_number = f"CASE-{str(last_number + 1).zfill(6)}"
        else:
            new_number = "CASE-000001"
        
        logger.info(f"Generated case number: {new_number}")
        
        # Create the case object
        db_case = Case(
            case_number=new_number,
            title=case.title,
            type=case.type,
            status=case.status,
            court=case.court,
            judge=case.judge,
            next_hearing=case.next_hearing,
            client_id=case.client_id,
            primary_attorney_id=case.primary_attorney_id
        )
        
        # Add to session and commit
        db.add(db_case)
        await db.commit()
        await db.refresh(db_case)
        
        logger.info(f"Case created successfully with ID: {db_case.id}")
        return db_case
        
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error in create_case: {e}")
        return None
    except Exception as e:
        await db.rollback()
        logger.error(f"Unexpected error in create_case: {e}")
        return None

async def update_case(
    db: AsyncSession, 
    case_id: str, 
    case_in: Union[CaseUpdate, Dict[str, Any]]
) -> Optional[Case]:
    """
    Update an existing case.
    """
    try:
        # Get the case
        case = await get_case(db, case_id=case_id)
        if not case:
            logger.warning(f"Case not found for update: {case_id}")
            return None
        
        # Prepare update data
        if isinstance(case_in, dict):
            update_data = case_in
        else:
            update_data = case_in.model_dump(exclude_unset=True)
        
        # Update the case attributes
        for field, value in update_data.items():
            setattr(case, field, value)
        
        # Commit changes
        await db.commit()
        await db.refresh(case)
        
        logger.info(f"Case updated successfully: {case_id}")
        return case
        
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error in update_case: {e}")
        return None
    except Exception as e:
        await db.rollback()
        logger.error(f"Unexpected error in update_case: {e}")
        return None

async def delete_case(db: AsyncSession, case_id: str) -> bool:
    """
    Delete a case by ID.
    """
    try:
        # Get the case
        case = await get_case(db, case_id=case_id)
        if not case:
            logger.warning(f"Case not found for deletion: {case_id}")
            return False
        
        # Delete the case
        await db.delete(case)
        await db.commit()
        
        logger.info(f"Case deleted successfully: {case_id}")
        return True
        
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error in delete_case: {e}")
        return False
    except Exception as e:
        await db.rollback()
        logger.error(f"Unexpected error in delete_case: {e}")
        return False 
