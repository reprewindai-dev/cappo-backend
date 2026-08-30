import re

with open('cappo_backend/identity/models.py', 'r') as f:
    text = f.read()

bad = """    rights: List[str]
    inbound_truth_state: str = "ADMISSIBLE"
    required_truth_state: str = "ADMISSIBLE"
    issued_at: int
    expires_at: int
    proof_of_possession: str"""

good = """    rights: List[str]
    issued_at: int
    expires_at: int
    proof_of_possession: str
    inbound_truth_state: str = "ADMISSIBLE"
    required_truth_state: str = "ADMISSIBLE\""""

text = text.replace(bad, good)

with open('cappo_backend/identity/models.py', 'w') as f:
    f.write(text)
