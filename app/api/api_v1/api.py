from fastapi import APIRouter
from app.api.api_v1.endpoints import auth, users, clients, cases, documents, events, health, invoices, legal_ai, legal_documents, offices, templates, library

api_router = APIRouter()

api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["authentication"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(offices.router, prefix="/offices", tags=["offices"])
api_router.include_router(clients.router, prefix="/clients", tags=["clients"])
api_router.include_router(cases.router, prefix="/cases", tags=["cases"])
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])
api_router.include_router(events.router, prefix="/events", tags=["events"])
api_router.include_router(invoices.router, prefix="/invoices", tags=["invoices"])
api_router.include_router(templates.router, prefix="/templates", tags=["templates"])
api_router.include_router(library.router, prefix="/legal-ai/library", tags=["library"])
api_router.include_router(legal_documents.router, prefix="/legal_documents", tags=["legal_documents"])
api_router.include_router(legal_ai.router, prefix="/legal-ai", tags=["legal-ai"]) 
