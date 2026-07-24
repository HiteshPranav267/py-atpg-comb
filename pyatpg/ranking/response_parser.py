import json
import re
from typing import List, Dict, Any


class MalformedRankingError(Exception):
    """Raised when the LLM output does not match the expected schema."""
    pass


class ResponseParser:
    """Parses raw LLM text into validated JSON structures."""

    def parse(self, raw_text: str) -> List[Dict[str, Any]]:
        """Parses and validates LLM ranking output.
        
        Returns:
            A list of dicts representing the ranked gates.
        """
        if not raw_text or not raw_text.strip():
            raise MalformedRankingError("LLM response was empty.")

        text = raw_text.strip()
        
        # Strip markdown json block if present
        if text.startswith("```"):
            # find first newline after ```
            first_nl = text.find("\n")
            if first_nl != -1:
                text = text[first_nl+1:]
            
            # strip trailing ```
            if text.endswith("```"):
                text = text[:-3].strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise MalformedRankingError(f"Failed to parse LLM response as JSON: {e}") from e

        if not isinstance(data, dict):
            raise MalformedRankingError("JSON root must be an object.")

        if "ranking" not in data:
            raise MalformedRankingError("JSON must contain a 'ranking' key.")

        ranking = data["ranking"]
        if not isinstance(ranking, list):
            raise MalformedRankingError("'ranking' must be a list.")

        # Validate elements
        for item in ranking:
            if not isinstance(item, dict):
                raise MalformedRankingError("Elements of 'ranking' must be objects.")
            if "gate" not in item:
                raise MalformedRankingError("Each ranking item must have a 'gate' key.")
            if "confidence" not in item:
                raise MalformedRankingError("Each ranking item must have a 'confidence' key.")

        return ranking
