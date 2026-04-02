from pydantic import BaseModel
from typing import List, Optional

class UserLogin(BaseModel):
    username: str
    password: str

class ConfigUpdate(BaseModel):
    regex_placa: str
    palavra_chave: str
    evo_url: Optional[str] = ""
    evo_instance: Optional[str] = ""
    evo_apikey: Optional[str] = ""
    msg_erro_placa: Optional[str] = ""
