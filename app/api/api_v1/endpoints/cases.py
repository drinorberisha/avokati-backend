from typing import List, Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.crud import case as case_crud
from app.schemas.case import Case, CaseCreate, CaseUpdate, CaseResponse
from app.core.auth import get_current_user
from app.schemas.user import User, UserRole

router = APIRouter()

@router.post("/", response_model=Case)
def create_case(
    *,
    db: Session = Depends(get_db),
    case_in: CaseCreate,
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Create new case.
    Requires attorney or admin role.
    """
    print("\n=== Starting case creation in API endpoint ===")
    print(f"Received case data: {case_in.model_dump()}")
    print(f"Current user role: {current_user.role}")
    print(f"Case status before validation: {case_in.status}")
    print(f"Case status type: {type(case_in.status)}")

    if current_user.role not in [UserRole.ATTORNEY, UserRole.ADMIN]:
        print(f"Authorization failed. User role: {current_user.role}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only attorneys can create cases"
        )
    
    # Verify case number is unique
    existing_case = case_crud.get_case_by_number(db, case_number=case_in.case_number)
    if existing_case:
        print(f"Case number {case_in.case_number} already exists")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Case number already exists"
        )
    
    try:
        print("Attempting to create case in database")
        new_case = case_crud.create_case(db, case_in)
        print(f"Case created successfully. New case status: {new_case.status}")
        return new_case
    except Exception as e:
        print(f"Error creating case in API endpoint: {str(e)}")
        print(f"Error type: {type(e)}")
        raise

@router.get("/", response_model=List[CaseResponse])
async def get_cases(
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Retrieve cases.
    """
    return case_crud.get_cases(db, skip=0, limit=100)

@router.get("/{case_id}", response_model=CaseResponse)
def read_case(
    *,
    db: Session = Depends(get_db),
    case_id: str,
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Get case by ID.
    """
    case = case_crud.get_case(db, case_id=case_id)
    if not case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Case not found"
        )
    return case

@router.put("/{case_id}", response_model=Case)
def update_case(
    *,
    db: Session = Depends(get_db),
    case_id: str,
    case_in: CaseUpdate,
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Update case.
    Only the assigned attorney or admin can update the case.
    """
    case = case_crud.get_case(db, case_id=case_id)
    if not case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Case not found"
        )
    
    # Check if user has permission to update
    if (current_user.role != UserRole.ADMIN and 
        current_user.id != case.primary_attorney_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the assigned attorney or admin can update the case"
        )
    
    return case_crud.update_case(db, case=case, case_in=case_in)

@router.delete("/{case_id}", response_model=Case)
def delete_case(
    *,
    db: Session = Depends(get_db),
    case_id: str,
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Delete case.
    Only admin can delete cases.
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin can delete cases"
        )
    
    case = case_crud.get_case(db, case_id=case_id)
    if not case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Case not found"
        )
    return case_crud.delete_case(db, case_id=case_id) 