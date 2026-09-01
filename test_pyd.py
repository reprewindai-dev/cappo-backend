from pydantic import BaseModel, ConfigDict
class A(BaseModel):
    model_config = ConfigDict(frozen=True)
    x: int
    y: str | None = None
a = A(x=1)
b = a.model_copy(update={'y': 'test'})
print(b.y)
