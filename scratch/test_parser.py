import json

def parse_llm_response(text: str) -> str:
    text = text.strip()
    
    # Check if SSE stream (data: ...)
    if "data:" in text:
        chunks = []
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                payload_str = line[5:].strip()
                if payload_str and payload_str != "[DONE]":
                    try:
                        obj = json.loads(payload_str)
                        choice = obj.get("choices", [{}])[0]
                        chunk = choice.get("delta", {}).get("content") or choice.get("message", {}).get("content") or ""
                        if chunk:
                            chunks.append(chunk)
                    except Exception:
                        pass
        if chunks:
            return "".join(chunks)

    # Standard JSON parse or raw_decode
    try:
        data = json.loads(text)
    except Exception:
        try:
            data, _ = json.JSONDecoder().raw_decode(text)
        except Exception:
            raise ValueError(f"Resposta inválida da IA: {text[:150]}")
            
    if isinstance(data, dict):
        choices = data.get("choices", [])
        if choices:
            c = choices[0]
            if isinstance(c, dict):
                msg = c.get("message", {})
                if isinstance(msg, dict) and "content" in msg and msg["content"] is not None:
                    return str(msg["content"])
                delta = c.get("delta", {})
                if isinstance(delta, dict) and "content" in delta and delta["content"] is not None:
                    return str(delta["content"])
                if "text" in c and c["text"] is not None:
                    return str(c["text"])
    raise ValueError(f"Formato de resposta inesperado da IA: {text[:150]}")

# Tests
t1 = '{"choices": [{"message": {"content": "IA funcionando"}}]}\n{"extra": 123}'
t2 = 'data: {"choices": [{"delta": {"content": "IA "}}]}\n\ndata: {"choices": [{"delta": {"content": "funcionando"}}]}\n\ndata: [DONE]'

print("Test 1:", parse_llm_response(t1))
print("Test 2:", parse_llm_response(t2))
