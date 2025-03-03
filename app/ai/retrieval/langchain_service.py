import logging
from typing import List, Dict, Any, Optional, Tuple
from langchain_openai import ChatOpenAI
from langchain.chains import RetrievalQA
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough

from app.core.config import settings
from app.ai.retrieval.vector_store import vector_store_client

logger = logging.getLogger(__name__)

# Initialize LLM
llm = ChatOpenAI(
    model=settings.OPENAI_MODEL,
    temperature=0.1,
    openai_api_key=settings.OPENAI_API_KEY
)

# Define prompt template for legal QA
LEGAL_QA_TEMPLATE = """
You are an expert legal assistant for lawyers in Kosovo. Use the following pieces of legal context to answer the question at the end.
If you don't know the answer, just say that you don't know, don't try to make up an answer.
Keep your answers concise and focused on the legal aspects.

CONTEXT:
{context}

QUESTION: {question}

YOUR ANSWER:
"""

LEGAL_QA_PROMPT = PromptTemplate.from_template(LEGAL_QA_TEMPLATE)


class LangChainService:
    """Service for document retrieval and question answering using LangChain."""
    
    def __init__(self):
        """Initialize the LangChain service."""
        self.vector_store = vector_store_client
    
    async def index_documents(
        self, texts: List[str], metadatas: List[Dict[str, Any]]
    ) -> List[str]:
        """
        Index documents in the vector store.
        
        Args:
            texts: List of document texts
            metadatas: List of metadata dictionaries for each document
            
        Returns:
            List of vector IDs
        """
        return await self.vector_store.add_documents(texts, metadatas)
    
    async def delete_documents(self, ids: List[str]) -> None:
        """
        Delete documents from the vector store.
        
        Args:
            ids: List of document IDs to delete
        """
        await self.vector_store.delete(ids)
    
    async def answer_question(
        self, 
        question: str, 
        filter: Optional[Dict[str, Any]] = None,
        top_k: int = 5
    ) -> Dict[str, Any]:
        """
        Answer a legal question using the indexed documents.
        
        Args:
            question: The legal question to answer
            filter: Optional filter for document retrieval
            top_k: Number of documents to retrieve
            
        Returns:
            Dictionary with answer and source documents
        """
        # Retrieve relevant documents
        docs_and_scores = await self.vector_store.search(
            query=question,
            filter=filter,
            top_k=top_k
        )
        
        # Extract documents and scores
        docs = [doc for doc, _ in docs_and_scores]
        scores = [score for _, score in docs_and_scores]
        
        if not docs:
            return {
                "answer": "I couldn't find any relevant legal information to answer your question.",
                "sources": [],
                "scores": []
            }
        
        # Format context from retrieved documents
        context_texts = [f"Document {i+1}:\n{doc.page_content}\n" for i, doc in enumerate(docs)]
        context = "\n".join(context_texts)
        
        # Create retrieval chain
        qa_chain = (
            {"context": lambda _: context, "question": RunnablePassthrough()}
            | LEGAL_QA_PROMPT
            | llm
            | StrOutputParser()
        )
        
        # Run the chain
        answer = qa_chain.invoke(question)
        
        # Format source documents
        sources = []
        for i, doc in enumerate(docs):
            metadata = doc.metadata.copy()
            # Remove chunk-specific metadata
            if "chunk" in metadata:
                del metadata["chunk"]
            if "total_chunks" in metadata:
                del metadata["total_chunks"]
            
            sources.append({
                "content": doc.page_content,
                "document_metadata": metadata,
                "score": scores[i]
            })
        
        return {
            "answer": answer,
            "sources": sources,
            "scores": scores
        }
    
    async def retrieve_similar_documents(
        self, 
        query: str, 
        filter: Optional[Dict[str, Any]] = None,
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Retrieve documents similar to the query.
        
        Args:
            query: The search query
            filter: Optional filter for document retrieval
            top_k: Number of documents to retrieve
            
        Returns:
            List of documents with metadata and similarity scores
        """
        docs_and_scores = await self.vector_store.search(
            query=query,
            filter=filter,
            top_k=top_k
        )
        
        results = []
        for doc, score in docs_and_scores:
            results.append({
                "content": doc.page_content,
                "document_metadata": doc.metadata,
                "score": score
            })
        
        return results


# Singleton instance
langchain_service = LangChainService() 