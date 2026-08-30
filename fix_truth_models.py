import re

with open('cappo_backend/truth/models.py', 'r') as f:
    text = f.read()

bad = """    UNRESOLVED_CONFLICT = "UNRESOLVED_CONFLICT"
    REJECTED = "REJECTED\""""

good = """    UNRESOLVED = "UNRESOLVED"
    CONFLICTED = "CONFLICTED"
    UNAVAILABLE = "UNAVAILABLE"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED\""""

text = text.replace(bad, good)

# Also let's add `corroboration_required: bool = False` to FactRequirement
req_bad = """class FactRequirement(BaseModel):
    fact_domain: str
    minimum_assurance: str
    max_age_seconds: int
    authority_class: Optional[str] = None"""

req_good = """class FactRequirement(BaseModel):
    fact_domain: str
    minimum_assurance: str
    max_age_seconds: int
    authority_class: Optional[str] = None
    corroboration_required: bool = False"""

text = text.replace(req_bad, req_good)

with open('cappo_backend/truth/models.py', 'w') as f:
    f.write(text)
