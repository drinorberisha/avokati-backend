from typing import List, Optional
from sqlalchemy.orm import Session, joinedload
from app.db.models import Case, CaseStatus as DBCaseStatus
from app.schemas.case import CaseCreate, CaseUpdate

def get_case(db: Session, case_id: str) -> Optional[Case]:
    return db.query(Case).options(joinedload(Case.client)).filter(Case.id == case_id).first()

def get_case_by_number(db: Session, case_number: str) -> Optional[Case]:
    return db.query(Case).options(joinedload(Case.client)).filter(Case.case_number == case_number).first()

def get_cases(db: Session, skip: int = 0, limit: int = 100) -> List[Case]:
    return db.query(Case).options(joinedload(Case.client)).offset(skip).limit(limit).all()

def get_client_cases(db: Session, client_id: str) -> List[Case]:
    return db.query(Case).options(joinedload(Case.client)).filter(Case.client_id == client_id).all()

def create_case(db: Session, case: CaseCreate) -> Case:
    print("\n=== Starting case creation in CRUD layer ===")
    print(f"Received case data: {case.model_dump()}")
    print(f"Status value: {case.status!r}")
    print(f"Status type: {type(case.status)}")
    print(f"Status value type: {type(case.status.value) if hasattr(case.status, 'value') else 'N/A'}")
    
    try:
        # Create the database object with the status value directly
        db_case = Case(
            case_number=case.case_number,
            title=case.title,
            type=case.type,
            status=case.status.value.lower(),  # Ensure status is lowercase
            court=case.court,
            judge=case.judge,
            next_hearing=case.next_hearing,
            client_id=case.client_id,
            primary_attorney_id=case.primary_attorney_id
        )
        
        # Add to session
        print("Adding case to database session...")
        db.add(db_case)
        
        # Commit the transaction
        print("Committing to database...")
        try:
            db.commit()
            print("Successfully committed to database")
        except Exception as commit_error:
            print(f"Error during commit: {str(commit_error)}")
            print(f"Commit error type: {type(commit_error)}")
            db.rollback()
            raise
        
        # Refresh the object
        print("Refreshing case object...")
        db.refresh(db_case)
        print(f"Final case status after refresh: {db_case.status!r}")
        
        return db_case
        
    except Exception as e:
        print(f"Error in create_case: {str(e)}")
        print(f"Error type: {type(e)}")
        print("Stack trace:", e.__traceback__)
        raise

def update_case(db: Session, case: Case, case_in: CaseUpdate) -> Case:
    update_data = case_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(case, field, value)
    db.commit()
    db.refresh(case)
    return case

def delete_case(db: Session, case_id: str) -> Case:
    case = get_case(db, case_id=case_id)
    db.delete(case)
    db.commit()
    return case 