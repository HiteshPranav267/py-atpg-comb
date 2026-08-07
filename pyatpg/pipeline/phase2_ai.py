import sys

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None

class AIPrioritizer:
    """Phase 2: AI Prioritization Brain"""
    
    def __init__(self, api_key=None):
        self.api_key = api_key
        if api_key and genai is None:
            print("Warning: google-genai library not found. Falling back to offline heuristics.")
            print("To use the LLM, run: pip install google-genai")
            self.api_key = None

    def get_offline_analysis(self, profiles):
        """Fallback if no API key is provided: heuristic-based prioritization."""
        suspicious = []
        for p in profiles:
            f = p['features']
            # Trust-Hub heuristics: Low toggling, high fan-in, low observability (high CO value)
            if f['toggle_rate'] < 0.1 and f['fan_in'] >= 2 and f['co'] > 5:
                suspicious.append(p['gate_id'])
        return suspicious[:30]

    def get_llm_analysis(self, summary, profiles):
        """Use Gemini to perform Few-Shot prioritization."""
        client = genai.Client(api_key=self.api_key)
        
        prompt = f"""
{summary}

You are a Hardware Security Expert specializing in Hardware Trojan detection.
A hardware trojan typically consists of a Trigger (rarely activated, low toggle rate, high fan-in) and a Payload (hard to observe, high SCOAP CO value).

Below are the structural profiles of the gates in the circuit.
Your task: Identify the top most suspicious gates that should be targeted by the ATPG Oracle for deeper inspection.
Only return the exact gate IDs, separated by commas, nothing else.

Profiles:
"""
        # Limit to top 200 gates by heuristic to fit in prompt if circuit is huge
        profiles_copy = list(profiles)
        profiles_copy.sort(key=lambda x: (x['features']['toggle_rate'], -x['features']['co']))
        for p in profiles_copy[:200]:
            prompt += p['profile'] + "\n"
            
        response = client.models.generate_content(
            model='gemini-flash-latest',
            contents=prompt,
        )
        
        # Parse comma separated response
        text = response.text.replace('\n', '').strip()
        return [g.strip() for g in text.split(',') if g.strip()]

    def prioritize(self, profiles, summary=""):
        """Run prioritization based on available credentials."""
        print("[Phase 2] Prioritizing suspicious gates for Oracle...")
        if self.api_key:
            print("[Phase 2] Using LLM API for Few-Shot Prioritization...")
            gates = self.get_llm_analysis(summary, profiles)
        else:
            print("[Phase 2] Running heuristic offline analysis (no API key)...")
            gates = self.get_offline_analysis(profiles)
            
        print(f"[Phase 2] Identified {len(gates)} suspicious gates for targeted ATPG.")
        return gates
