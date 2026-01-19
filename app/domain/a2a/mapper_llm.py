from typing import List, Dict, Any, Optional
from app.utils.id import uuid7
from datetime import datetime, timezone

# We use dicts representing the JSON structures for A2A to avoid tight coupling with specific Pydantic models
# internal "ChatMessage" is simple dict usually: {"role": str, "content": str}

def map_input_to_llm_messages(a2a_input: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """
    Maps A2A Input messages to OpenAI-format messages.
    A2A Message: { role, content: [{text...}, {blob...}] }
    OpenAI Message: { role, content: str } (Simplest text-only mapping for A1)
    """
    llm_messages = []
    
    for msg in a2a_input:
        role = msg.get("role", "user")
        content_parts = msg.get("content", [])
        
        # Aggregate text parts
        text_content = ""
        for part in content_parts:
            if "text" in part:
                text_content += part["text"]
            # Ignore blobs for text-only LLM in A1 or handle basic image_url later
            
        llm_messages.append({
            "role": role,
            "content": text_content
        })
        
    return llm_messages


def map_llm_response_to_task(
    llm_response: Dict[str, Any], 
    profile: Dict[str, str],
    original_task_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Maps an OpenAI Chat Completion response to an A2A Task object.
    """
    # Generate Task ID if not provided (though A2A request usually creates one implicitly? No, response has params task_id?)
    # For tasks.send sync, we return a COMPLETED task.
    
    task_id = original_task_id or uuid7()
    
    now_iso = datetime.now(timezone.utc).isoformat() + "Z"
    
    # Extract content
    choices = llm_response.get("choices", [])
    content_text = ""
    if choices:
        message = choices[0].get("message", {})
        content_text = message.get("content", "")
        
    # Create Artifact/Output
    # A2A Task has "artifacts" which can be the response text
    output_artifact = {
        "artifact_id": uuid7(),
        "type": "text/plain",
        "name": "response.txt",
        "content": {
            "text": content_text
        }
    }
    
    task = {
        "profile": profile,
        "task_id": task_id,
        "status": "completed",
        "created_at": now_iso, # Approximation
        "updated_at": now_iso,
        "artifacts": [output_artifact],
        # "input": ... (optional to echo back)
    }
    
    return task
