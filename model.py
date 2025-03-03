import getpass
import os
import re
from fuzzywuzzy import process


os.environ["OPENAI_API_KEY"] = "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"

from langchain.chat_models import init_chat_model
llm = init_chat_model("gpt-4o-mini", model_provider="openai")

from langchain_openai import OpenAIEmbeddings
embeddings = OpenAIEmbeddings(model="text-embedding-3-large")

from langchain_core.vectorstores import InMemoryVectorStore
vector_store = InMemoryVectorStore(embeddings)

# Load the document file
from langchain_community.document_loaders import TextLoader
from langchain_core.documents import Document

file_path = "kodicivil-en.txt"
loader = TextLoader(file_path, encoding="utf-8")
docs = loader.load()

# Define a custom splitter that splits by article boundaries.
def custom_article_splitter(document: Document):
    """
    Splits the document into articles using a regex pattern.
    Each split will start with "Article <number>".
    """
    text = document.page_content
    # The lookahead pattern ensures we split before every occurrence of "Article" followed by digits.
    pattern = r"(?=Article \d+)"
    splits = re.split(pattern, text)
    # Remove any empty chunks and trim whitespace.
    articles = [chunk.strip() for chunk in splits if chunk.strip()]
    # Return a list of new Document objects for each article.
    return [Document(page_content=article) for article in articles]

# Apply the custom splitter to each loaded document.
all_splits = []
for doc in docs:
    all_splits.extend(custom_article_splitter(doc))

# Index the document splits into the vector store.
_ = vector_store.add_documents(documents=all_splits)

# Set up the graph and tools.
from langgraph.graph import MessagesState, StateGraph
graph_builder = StateGraph(MessagesState)

# Storage for conversation context
conversation_memory = {
    "last_article": None,
    "last_article_content": None,
    "messages": []
}

from langchain_core.tools import tool
@tool(response_format="content_and_artifact")
def retrieve(query: str):
    """Retrieve information related to a query."""
    
    # Check if query is a follow-up about an article previously mentioned
    if any(term in query.lower() for term in ["example", "this law", "this article", "where used"]) and not re.search(r'article \d+', query.lower()):
        if conversation_memory["last_article"]:
            # Create a more focused query based on the last article
            article_num = conversation_memory["last_article"]
            article_content = conversation_memory["last_article_content"]
            
            # Custom queries based on the content type
            if "joint and several liability" in article_content.lower() or "liable jointly" in article_content.lower():
                formatted_query = f"joint and several liability examples article {article_num}"
            elif "monetary compensation" in article_content.lower():
                formatted_query = f"monetary compensation liability article {article_num} examples"
            else:
                formatted_query = f"examples of application article {article_num}"
                
            print(f"📝 Reformulated query: {formatted_query}")
        else:
            formatted_query = query  # Fallback if no previous article found
    else:
        # Extract article number
        match = re.search(r'\d+', query)
        if match:
            article_number = match.group(0)
            formatted_query = f"Article {article_number}"
            # Store for future reference
            conversation_memory["last_article"] = article_number
        else:
            formatted_query = query  # Fallback if no number found

    # First, try an exact match search
    for doc in all_splits:
        if formatted_query in doc.page_content:
            # Store article content for context in follow-up questions
            if "last_article" in conversation_memory and conversation_memory["last_article"]:
                conversation_memory["last_article_content"] = doc.page_content
            return doc.page_content, [doc]  # Return exact match immediately

    # If no exact match, fallback to similarity search
    retrieved_docs = vector_store.similarity_search(formatted_query, k=5)

    # Debugging: Print search results
    print(f"🔍 Search Results for: {formatted_query}")
    for doc in retrieved_docs:
        print(f"➡️ Found in: {doc.metadata}, Content Preview: {doc.page_content[:200]}...")

    # Use Fuzzy Matching to Find Best Article
    best_match = None
    best_score = 0
    for doc in retrieved_docs:
        doc_text = doc.page_content
        match_score = process.extractOne(formatted_query, doc_text.split("\n"))[1]
        if match_score > best_score:
            best_match = doc_text
            best_score = match_score

    if best_match:
        return best_match, retrieved_docs
    else:
        # If no match found but we have previous article context, return that for reference
        if conversation_memory["last_article_content"] and "this" in query.lower():
            return f"No specific examples found, but for reference, here is the previously discussed article:\n\n{conversation_memory['last_article_content']}", retrieved_docs
        return "⚠️ No matching article found.", retrieved_docs


from langchain_core.messages import SystemMessage
from langgraph.prebuilt import ToolNode

# Step 1: Generate an AIMessage that may include a tool-call.
def query_or_respond(state: MessagesState):
    # Store the current message in memory
    if state["messages"] and state["messages"][-1].type == "human":
        conversation_memory["messages"].append({"role": "user", "content": state["messages"][-1].content})
    
    llm_with_tools = llm.bind_tools([retrieve])
    response = llm_with_tools.invoke(state["messages"])
    return {"messages": [response]}

# Step 2: Execute the retrieval.
tools = ToolNode([retrieve])

# Step 3: Generate the final answer using the retrieved content.
def generate(state: MessagesState):
    recent_tool_messages = []
    for message in reversed(state["messages"]):
        if message.type == "tool":
            recent_tool_messages.append(message)
        else:
            break
    tool_messages = recent_tool_messages[::-1]
    docs_content = "\n\n".join(msg.content for msg in tool_messages)
    
    # Get previous article information if available
    previous_article_context = ""
    if conversation_memory["last_article"] and conversation_memory["last_article_content"]:
        previous_article_context = f"\n\nPreviously, you retrieved Article {conversation_memory['last_article']} which states: \n{conversation_memory['last_article_content']}"
    
    system_message_content = (
        "You are an expert in Kosovo law with complete and authoritative knowledge of its legal system, "  
        "including statutes, regulations, case law, and legal precedents. "  
        "Use the following retrieved legal documents to generate a well-reasoned and legally accurate answer to the query. "  
        "Your response must be based primarily on the provided legal content, ensuring precision and adherence to Kosovo's legal framework. "  
        "If the retrieved documents don't contain all necessary information but you previously retrieved relevant article content, "
        "use that knowledge to provide general context or examples of how such provisions typically apply in legal systems."
        "If you truly cannot provide an answer, explain what information would be needed. "
        f"{previous_article_context}\n\n"
        f"Retrieved content: {docs_content}"
    )
    
    conversation_messages = [
        message
        for message in state["messages"]
        if message.type in ("human", "system")
        or (message.type == "ai" and not message.tool_calls)
    ]
    prompt = [SystemMessage(system_message_content)] + conversation_messages
    response = llm.invoke(prompt)
    
    # Store the AI response in memory
    conversation_memory["messages"].append({"role": "assistant", "content": response.content})
    
    return {"messages": [response]}

from langgraph.graph import END
from langgraph.prebuilt import tools_condition

graph_builder.add_node(query_or_respond)
graph_builder.add_node(tools)
graph_builder.add_node(generate)

graph_builder.set_entry_point("query_or_respond")
graph_builder.add_conditional_edges(
    "query_or_respond",
    tools_condition,
    {END: END, "tools": "tools"},
)
graph_builder.add_edge("tools", "generate")
graph_builder.add_edge("generate", END)

graph = graph_builder.compile()

# Initialize the config dictionary to hold memory (conversation history)
config = {}

# ----------------- Turn 1 -----------------
input_message = "What does article 234 say"
print("================================ Human Message =================================")
print(input_message)
print("=================================== Ai Message ===================================")
for step in graph.stream(
    {"messages": [{"role": "user", "content": input_message}]},
    stream_mode="values",
    config=config,
):
    step["messages"][-1].pretty_print()

# ----------------- Turn 2 -----------------
input_message = "Can you look up some common examples where this law is used"
print("================================ Human Message =================================")
print(input_message)
print("=================================== Ai Message ===================================")
for step in graph.stream(
    {"messages": [{"role": "user", "content": input_message}]},
    stream_mode="values",
    config=config,
):
    step["messages"][-1].pretty_print()