import uuid

# Namespace for our application's IDs
# We use a constant namespace to ensure reproducibility across runs/machines
APP_NAMESPACE = uuid.UUID('6ba7b810-9dad-11d1-80b4-00c04fd430c8') # Using OID namespace as base

def generate_deterministic_id(*parts: str) -> str:
    """
    Generates a deterministic UUID string based on one or more input parts.
    
    Args:
        *parts: One or more strings to hash together (e.g., name, type).
        
    Returns:
        A 32-character hexadecimal UUID string.
    """
    if not parts or any(p is None for p in parts):
        return None
    
    # Normalize and join parts to ensure consistency
    combined_name = ":".join(str(p).strip().lower() for p in parts)
    
    # Generate UUID5 (SHA-1 hashing) which is deterministic
    generated_uuid = uuid.uuid5(APP_NAMESPACE, combined_name)
    
    return str(generated_uuid)
