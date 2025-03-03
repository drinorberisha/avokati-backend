from typing import List, Any, Optional, Dict
from fastapi import APIRouter, Depends, HTTPException, status, Query, Path
from sqlalchemy.ext.asyncio import AsyncSession
import logging
from app.core.database import get_db
from app.crud import case as case_crud
from app.schemas.case import Case, CaseCreate, CaseUpdate, CaseResponse, CaseStatus
from app.core.auth import get_current_user
from app.schemas.user import User, UserRole
from uuid import UUID

logger = logging.getLogger(__name__)
router = APIRouter()

@router.post("/", response_model=Case, status_code=status.HTTP_201_CREATED)
async def create_case(
    *,
    db: AsyncSession = Depends(get_db),
    case_in: CaseCreate,
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Create new case.
    
    Requires attorney or admin role.
    """
    logger.info(f"Case creation requested by user: {current_user.id}")
    
    # Check permissions
    if current_user.role not in [UserRole.attorney, UserRole.admin]:
        logger.warning(f"Unauthorized case creation attempt by user: {current_user.id}, role: {current_user.role}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only attorneys and admins can create cases"
        )
    
    # Create the case
    new_case = await case_crud.create_case(db, case_in)
    if not new_case:
        logger.error("Failed to create case")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create case"
        )
    
    logger.info(f"Case created successfully: {new_case.id}")
    return new_case

@router.get("/", response_model=List[CaseResponse])
async def get_cases(
    *,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    skip: int = Query(0, ge=0, description="Skip N records"),
    limit: int = Query(100, ge=1, le=100, description="Limit to N records"),
    client_id: Optional[UUID] = Query(None, description="Filter by client ID"),
    status: Optional[CaseStatus] = Query(None, description="Filter by case status")
) -> Any:
    """
    Retrieve cases with optional filtering.
    
    Supports pagination and filtering by client_id and status.
    Clients can only see their own cases.
    """
    logger.info(f"Case list requested by user: {current_user.id}")
    
    # Prepare filters
    filters: Dict[str, Any] = {}
    
    # If user is a client, only show their cases
    if current_user.role == UserRole.client:
        filters["client_id"] = current_user.id
    elif client_id:
        filters["client_id"] = client_id
    
    # Add status filter if provided
    if status:
        filters["status"] = status
    
    # Get cases with filters
    cases = await case_crud.get_cases(db, skip=skip, limit=limit, filters=filters)
    
    logger.info(f"Retrieved {len(cases)} cases")
    return cases

@router.get("/{case_id}", response_model=CaseResponse)
async def read_case(
    *,
    db: AsyncSession = Depends(get_db),
    case_id: str = Path(..., description="The ID of the case to retrieve"),
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Get case by ID.
    
    Clients can only view their own cases.
    """
    logger.info(f"Case {case_id} requested by user: {current_user.id}")
    
    # Get the case
    case = await case_crud.get_case(db, case_id=case_id)
    if not case:
        logger.warning(f"Case not found: {case_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Case not found"
        )
    
    # Check if user has permission to view this case
    if (current_user.role == UserRole.client and 
        current_user.id != str(case.client_id)):
        logger.warning(f"Unauthorized case access attempt by user: {current_user.id}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to view this case"
        )
    
    return case

@router.put("/{case_id}", response_model=Case)
async def update_case(
    *,
    db: AsyncSession = Depends(get_db),
    case_id: str = Path(..., description="The ID of the case to update"),
    case_in: CaseUpdate,
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Update case.
    
    Only the assigned attorney or admin can update the case.
    """
    logger.info(f"Case update requested for {case_id} by user: {current_user.id}")
    
    # Get the case
    case = await case_crud.get_case(db, case_id=case_id)
    if not case:
        logger.warning(f"Case not found for update: {case_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Case not found"
        )
    
    # Check if user has permission to update
    if (current_user.role != UserRole.admin and 
        current_user.id != str(case.primary_attorney_id)):
        logger.warning(f"Unauthorized case update attempt by user: {current_user.id}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the assigned attorney or admin can update the case"
        )
    
    # Update the case
    updated_case = await case_crud.update_case(db, case_id=case_id, case_in=case_in)
    if not updated_case:
        logger.error(f"Failed to update case: {case_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update case"
        )
    
    logger.info(f"Case updated successfully: {case_id}")
    return updated_case

@router.delete("/{case_id}", response_model=Dict[str, bool])
async def delete_case(
    *,
    db: AsyncSession = Depends(get_db),
    case_id: str = Path(..., description="The ID of the case to delete"),
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Delete case.
    
    Only admin can delete cases.
    """
    logger.info(f"Case deletion requested for {case_id} by user: {current_user.id}")
    
    # Check if user has permission to delete
    if current_user.role != UserRole.admin:
        logger.warning(f"Unauthorized case deletion attempt by user: {current_user.id}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin can delete cases"
        )
    
    # Check if case exists
    case = await case_crud.get_case(db, case_id=case_id)
    if not case:
        logger.warning(f"Case not found for deletion: {case_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Case not found"
        )
    
    # Delete the case
    success = await case_crud.delete_case(db, case_id=case_id)
    if not success:
        logger.error(f"Failed to delete case: {case_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete case"
        )
    
    logger.info(f"Case deleted successfully: {case_id}")
    return {"success": True}

@router.get("/by-number/{case_number}", response_model=CaseResponse)
async def get_case_by_number(
    *,
    db: AsyncSession = Depends(get_db),
    case_number: str = Path(..., description="The case number to retrieve"),
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Get case by case number.
    
    Clients can only view their own cases.
    """
    logger.info(f"Case lookup by number {case_number} requested by user: {current_user.id}")
    
    # Get the case
    case = await case_crud.get_case_by_number(db, case_number=case_number)
    if not case:
        logger.warning(f"Case not found by number: {case_number}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Case not found"
        )
    
    # Check if user has permission to view this case
    if (current_user.role == UserRole.client and 
        current_user.id != str(case.client_id)):
        logger.warning(f"Unauthorized case access attempt by user: {current_user.id}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to view this case"
        )
    
    return case 