import os
from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """Abstract base class for LLM providers.
    
    Can be implemented for Gemini, OpenAI, Claude, Ollama, etc.
    """

    @abstractmethod
    def complete(self, prompt: str) -> str:
        """Sends the prompt to the LLM and returns the raw string response."""
        ...


class GeminiProvider(LLMProvider):
    """Implementation of LLMProvider using Google Gemini SDK."""

    def __init__(self, model_name: str = "gemini-flash-latest"):
        self.model_name = model_name
        self._client = None
        
    def _get_client(self):
        try:
            from google import genai
        except ImportError:
            raise ImportError("Please install google-genai to use GeminiProvider")
            
        if not self._client:
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                raise ValueError("GEMINI_API_KEY environment variable is not set.")
            self._client = genai.Client(api_key=api_key)
        return self._client

    def complete(self, prompt: str) -> str:
        client = self._get_client()
        try:
            response = client.models.generate_content(
                model=self.model_name,
                contents=prompt
            )
            return response.text
        except Exception as e:
            raise RuntimeError(f"Gemini API failure: {str(e)}") from e
