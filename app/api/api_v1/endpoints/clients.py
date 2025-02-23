from typing import List, Any
from fastapi import APIRouter, Depends, HTTPException, status
from app.core.auth import get_current_user
from app.schemas.client import Client, ClientCreate, ClientUpdate
from app.schemas.user import User
from app.core.supabase import get_supabase_client

router = APIRouter()

@router.post("/", response_model=Client)
async def create_client(
    *,
    client_in: ClientCreate,
    current_user: User = Depends(get_current_user),
    supabase = Depends(get_supabase_client)
) -> Any:
    """
    Create new client.
    """
    try:
        # Check if client with same email exists
        response = supabase.table('clients').select("*").eq('email', client_in.email).execute()
        if response.data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A client with this email already exists"
            )
        
        # Create new client
        client_data = client_in.model_dump()
        
        # Insert the client and return the inserted row
        response = supabase.table('clients').insert(client_data).execute()
        
        if not response.data or len(response.data) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to create client"
            )
        
        # Fetch the created client to ensure we have all fields
        created_client = supabase.table('clients').select("*").eq('id', response.data[0]['id']).single().execute()
        
        if not created_client.data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to retrieve created client"
            )
            
        return created_client.data
        
    except Exception as e:
        print(f"Error creating client: {str(e)}")  # Debug print
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.get("/", response_model=List[Client])
async def get_clients(
    current_user: User = Depends(get_current_user),
    supabase = Depends(get_supabase_client)
) -> Any:
    """
    Retrieve clients.
    """
    try:
        response = supabase.table('clients').select("*").execute()
        return response.data
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.get("/{client_id}", response_model=Client)
async def read_client(
    *,
    client_id: str,
    current_user: User = Depends(get_current_user),
    supabase = Depends(get_supabase_client)
) -> Any:
    """
    Get client by ID.
    """
    try:
        response = supabase.table('clients').select("*").eq('id', client_id).single().execute()
        if not response.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Client not found"
            )
        return response.data
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.put("/{client_id}", response_model=Client)
async def update_client(
    *,
    client_id: str,
    client_in: ClientUpdate,
    current_user: User = Depends(get_current_user),
    supabase = Depends(get_supabase_client)
) -> Any:
    """
    Update client.
    """
    try:
        # Check if client exists
        response = supabase.table('clients').select("*").eq('id', client_id).single().execute()
        if not response.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Client not found"
            )
        
        # Update client
        response = supabase.table('clients').update({
            **client_in.dict(exclude_unset=True),
            'updated_at': 'now()'
        }).eq('id', client_id).execute()
        
        return response.data[0]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.delete("/{client_id}", response_model=Client)
async def delete_client(
    *,
    client_id: str,
    current_user: User = Depends(get_current_user),
    supabase = Depends(get_supabase_client)
) -> Any:
    """
    Delete client.
    """
    try:
        # Check if client exists
        response = supabase.table('clients').select("*").eq('id', client_id).single().execute()
        if not response.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Client not found"
            )
        
        # Delete client
        response = supabase.table('clients').delete().eq('id', client_id).execute()
        return response.data[0]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.get("/{client_id}/cases")
async def get_client_cases(
    client_id: str,
    current_user: User = Depends(get_current_user),
    supabase = Depends(get_supabase_client)
) -> Any:
    """
    Get all cases associated with a specific client.
    """
    try:
        # First verify the client exists
        client_response = supabase.table('clients').select("*").eq('id', client_id).single().execute()
        if not client_response.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Client not found"
            )

        # Get all cases for this client
        cases_response = supabase.table('cases')\
            .select(
                "*",
                count="exact"
            )\
            .eq('client_id', client_id)\
            .execute()

        # Get metrics for each case
        cases_with_metrics = []
        for case in cases_response.data:
            # Calculate response time (example metric)
            response_time = "24h"  # This should be calculated based on your business logic
            
            # Calculate document submission status
            docs_response = supabase.table('documents')\
                .select("*")\
                .eq('case_id', case['id'])\
                .execute()
            
            doc_submission = "timely"
            if not docs_response.data:
                doc_submission = "incomplete"
            
            # Calculate payment status
            payment_status = "paid"  # This should be fetched from your payments table
            
            # Calculate client engagement
            engagement = "high"  # This should be based on interaction metrics
            
            # Add metrics to case
            case['metrics'] = {
                "responseTime": response_time,
                "documentSubmission": doc_submission,
                "paymentStatus": payment_status,
                "clientEngagement": engagement,
                "satisfaction": 5  # This should be calculated from actual ratings
            }
            
            cases_with_metrics.append(case)

        return cases_with_metrics

    except Exception as e:
        print(f"Error fetching client cases: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.get("/{client_id}/metrics")
async def get_client_metrics(
    client_id: str,
    current_user: User = Depends(get_current_user),
    supabase = Depends(get_supabase_client)
) -> Any:
    """
    Get metrics for a specific client.
    """
    try:
        # First verify the client exists
        client_response = supabase.table('clients').select("*").eq('id', client_id).single().execute()
        if not client_response.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Client not found"
            )

        # Get all cases for this client
        cases_response = supabase.table('cases')\
            .select("*")\
            .eq('client_id', client_id)\
            .execute()

        total_cases = len(cases_response.data)
        active_cases = len([c for c in cases_response.data if c['status'] == 'open'])
        completed_cases = len([c for c in cases_response.data if c['status'] == 'closed'])

        # Calculate average response time (this should be based on your actual data)
        avg_response_time = "24h"

        # Calculate payment history (this should be based on your actual payment records)
        payment_history = "excellent"

        # Calculate satisfaction (this should be based on actual ratings)
        satisfaction = 4.5

        metrics = {
            "totalCases": total_cases,
            "activeCases": active_cases,
            "completedCases": completed_cases,
            "averageResponseTime": avg_response_time,
            "paymentHistory": payment_history,
            "satisfaction": satisfaction
        }

        return metrics

    except Exception as e:
        print(f"Error fetching client metrics: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) 