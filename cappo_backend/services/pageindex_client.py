import os

import httpx


class PageIndexIngestionService:
    """
    Submits documents to the PageIndex service and checks their indexing status.
    """
    def __init__(self, api_key: str | None = None, base_url: str = "https://api.pageindex.ai"):
        self.api_key = api_key or os.environ.get("PAGEINDEX_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {self.api_key}"}

    def submit_document(self, file_path: str) -> str:
        """
        Submits a PDF document for tree indexing.
        Returns the provider_doc_id.
        """
        if not self.api_key:
            raise ValueError("PAGEINDEX_API_KEY is not set.")
            
        with open(file_path, "rb") as f:
            files = {"file": (os.path.basename(file_path), f, "application/pdf")}
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    f"{self.base_url}/v1/documents", 
                    headers=self.headers, 
                    files=files
                )
                response.raise_for_status()
                return response.json().get("doc_id", "")

    def get_status(self, provider_doc_id: str) -> str:
        """
        Gets the current indexing status (e.g., 'pending', 'completed').
        """
        if not self.api_key:
            return "failed"
            
        with httpx.Client(timeout=10.0) as client:
            response = client.get(
                f"{self.base_url}/v1/documents/{provider_doc_id}", 
                headers=self.headers
            )
            response.raise_for_status()
            return response.json().get("status", "pending")
