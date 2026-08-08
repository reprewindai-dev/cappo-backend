```markdown
# cappo-backend Development Patterns

> Auto-generated skill from repository analysis

## Overview

This skill documents the core development patterns, coding conventions, and common workflows for the `cappo-backend` Python codebase. It covers how to extend API endpoints, update service logic, maintain security and middleware, manage integration tests, and update CI/CD pipelines. The guide is designed to help contributors quickly understand how to make effective, consistent changes to the repository.

## Coding Conventions

- **Language:** Python
- **Framework:** None detected (custom backend)
- **File Naming:** Use `snake_case` for all Python files.
    - Example: `exec_router.py`, `service_logic.py`
- **Import Style:** Use relative imports within the package.
    ```python
    from .models import UserModel
    from ..services import user_service
    ```
- **Export Style:** Use named exports (explicitly define what is exported).
    ```python
    def process_data(...):
        ...

    __all__ = ["process_data"]
    ```
- **Commit Messages:** Mixed style, often prefixed with `fix`. Aim for concise, descriptive messages (~65 characters).

## Workflows

### API Endpoint Extension
**Trigger:** When adding a new API route or modifying API behavior  
**Command:** `/new-api-endpoint`

1. Edit or create router files in `cappo_backend/api/routers/` (e.g., `exec_router.py`).
2. Update or create related service files in `cappo_backend/services/` as needed.
3. Update `cappo_backend/main.py` to register new routers or change main logic.
4. Add or update integration/unit tests in `tests/` or `cappo_backend/tests/integration/`.
5. Update documentation if required.

**Example: Adding a new router**
```python
# cappo_backend/api/routers/new_feature.py
from fastapi import APIRouter

router = APIRouter()

@router.get("/new-feature")
def get_new_feature():
    return {"message": "New feature"}
```
Register in `main.py`:
```python
from .api.routers import new_feature
app.include_router(new_feature.router)
```

### Service Logic Update
**Trigger:** When implementing or modifying backend service logic  
**Command:** `/update-service-logic`

1. Edit or create files in `cappo_backend/services/`.
2. Update related models in `cappo_backend/models/` or config files if needed.
3. Add or update tests for the service logic.
4. Update documentation if required.

**Example: Updating a service**
```python
# cappo_backend/services/user_service.py
def get_user_by_id(user_id: int):
    ...
```

### Security or Middleware Fix
**Trigger:** When a security or middleware bug is found or a refactor causes regressions  
**Command:** `/fix-security-middleware`

1. Edit or restore files in `cappo_backend/security/` or `cappo_backend/security/middleware.py`.
2. Update imports in dependent modules (e.g., routers, orchestrator, `main.py`).
3. Add or update relevant tests (e.g., `tests/test_middleware.py`).
4. Verify application startup and that all tests pass.

**Example: Restoring middleware**
```python
# cappo_backend/security/middleware.py
def security_middleware(request, call_next):
    # security logic
    return await call_next(request)
```

### Integration Test Extension
**Trigger:** When new features are added or existing ones are modified, requiring integration-level validation  
**Command:** `/add-integration-test`

1. Edit or create test files in `cappo_backend/tests/integration/` or `tests/`.
2. Update or create fixtures in `tests/conftest.py` if needed.
3. Run tests to ensure coverage and passing status.

**Example: Adding an integration test**
```python
# cappo_backend/tests/integration/test_new_feature.py
def test_new_feature(client):
    response = client.get("/new-feature")
    assert response.status_code == 200
```

### CI Pipeline Update
**Trigger:** When CI/CD needs to be adjusted for new requirements, bugfixes, or optimizations  
**Command:** `/update-ci-pipeline`

1. Edit files in `.github/workflows/` (e.g., `ci.yml`).
2. Update `Dockerfile` or `docker-compose.yml` if build/deploy process changes.
3. Update documentation if CI/CD process changes.

**Example: Updating a workflow**
```yaml
# .github/workflows/ci.yml
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: '3.9'
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Run tests
        run: pytest
```

## Testing Patterns

- **Framework:** Unknown (likely `pytest` or similar)
- **File Pattern:** Test files are named using `*.test.ts` (possibly for TypeScript), but Python tests are in `tests/` and `cappo_backend/tests/integration/`.
- **Integration Tests:** Placed in `cappo_backend/tests/integration/`.
- **Fixtures:** Use `tests/conftest.py` for shared fixtures.
- **Example Test:**
    ```python
    # tests/test_service_logic.py
    def test_get_user_by_id():
        user = get_user_by_id(1)
        assert user is not None
    ```

## Commands

| Command                | Purpose                                            |
|------------------------|---------------------------------------------------|
| /new-api-endpoint      | Add or update an API endpoint                     |
| /update-service-logic  | Implement or modify backend service logic         |
| /fix-security-middleware | Fix or restore security/middleware modules      |
| /add-integration-test  | Add or update integration tests                   |
| /update-ci-pipeline    | Update CI/CD workflow files                       |
```
