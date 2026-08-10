"""
LLM Analyzer Module for PyATPG
Member 1 Task: Automated AI API integration.

Sends circuit gate feature profiles to OpenAI/Anthropic APIs
and parses returned Trojan suspiciousness scores.
"""

import json
import os
import re


# ------------------------------------------------------------------ prompts
SYSTEM_PROMPT = """You are a Hardware Security Expert acting as the 'Prioritization Brain' for an Oracle-Guided Hardware Trojan Detection framework.

YOUR OBJECTIVE:
Your goal is to pinpoint a small, targeted list of suspicious gates to be analyzed by the Atalanta ATPG tool. By using your few-shot reasoning capabilities, you identify structural 'Stealth' that standard heuristics might miss.

CONTEXTUAL CLUES (What to look for):
1. **Structural Stealth**: Nodes with high SCOAP costs (CC/CO) are difficult to control and observe.
2. **Dynamic Stealth**: Nodes with extremely low Toggle Rates almost never move during functional operation.
3. **Anomalous Topology**: Watch for unusual Fan-in/Fan-out ratios or gates with very low Centrality (isolated logic).
4. **Trojan Patterns**: Recall patterns from Trust-Hub benchmarks (e.g., small comparators hidden in deep logic cones).

YOUR OUTPUT:
Respond ONLY with a valid JSON array. Each element must have:
{
  "gate_id": "<gate name>",
  "suspicion_score": <float 0.0 to 1.0>,
  "reason": "<reasoning based on the fusion of raw structural and dynamic metrics>"
}

Only include the top priority suspects (suspicion_score >= 0.3).
"""


def _build_user_prompt(circuit_summary, gate_profiles, past_experiences=None):
    """Build the user message from the circuit summary and gate profile list."""
    prompt = ""
    
    if past_experiences:
        prompt += "=== KNOWLEDGE BASE (Past Experiences) ===\n"
        prompt += "In previous runs, you have analyzed several circuits and identified potential Trojans:\n"
        for exp in past_experiences:
            prompt += f"- Circuit '{exp['name']}' ({exp['gates']} gates): {exp['verdict_count']} gates were flagged as suspicious.\n"
        prompt += "Use these patterns to inform your current analysis if applicable.\n\n"

    prompt += f"=== CURRENT CIRCUIT SUMMARY ===\n{circuit_summary}\n\n"
    prompt += "=== PER-GATE FEATURE PROFILES ===\n"
    for p in gate_profiles:
        prompt += p['profile'] + "\n"
    prompt += "\nAnalyze the above circuit and return your JSON suspicion scores."
    return prompt


# ------------------------------------------------------------------ LLM API
class LLMAnalyzer:
    """Wrapper for querying LLMs (OpenAI or Anthropic) for HT detection."""

    def __init__(self, provider='openai', api_key=None, model=None, base_url=None):
        """
        Args:
            provider: 'openai', 'anthropic', 'kimi', 'ollama', or 'custom'
            api_key: API key string.
            model: Model name override (e.g., 'moonshot-v1-32k').
            base_url: Custom API endpoint (e.g., 'https://api.moonshot.cn/v1').
        """
        self.provider = provider.lower()
        self.base_url = base_url

        # API Key Mapping
        if api_key:
            self.api_key = api_key
        elif self.provider == 'openai':
            self.api_key = os.environ.get('OPENAI_API_KEY', '')
        elif self.provider == 'anthropic':
            self.api_key = os.environ.get('ANTHROPIC_API_KEY', '')
        elif self.provider == 'kimi':
            self.api_key = os.environ.get('MOONSHOT_API_KEY', '')
            self.base_url = self.base_url or 'https://api.moonshot.cn/v1'
        elif self.provider == 'ollama':
            self.api_key = 'ollama' # Usually not needed
            self.base_url = self.base_url or 'http://localhost:11434/v1'
        else:
            self.api_key = os.environ.get('LLM_API_KEY', '')

        # Model Mapping
        if model:
            self.model = model
        elif self.provider == 'openai':
            self.model = 'gpt-4o'
        elif self.provider == 'anthropic':
            self.model = 'claude-sonnet-4-20250514'
        elif self.provider == 'kimi':
            self.model = 'moonshot-v1-32k'
        elif self.provider == 'ollama':
            self.model = 'llama3.1'
        else:
            self.model = 'gpt-4o'

    def analyze_circuit(self, circuit_summary, gate_profiles, past_experiences=None):
        """Send the circuit data to the LLM and return parsed suspicion scores.

        Returns:
            list of dicts: [{'gate_id': str, 'suspicion_score': float, 'reason': str}, ...]
        """
        user_prompt = _build_user_prompt(circuit_summary, gate_profiles, past_experiences)

        if self.provider in ['openai', 'kimi', 'ollama', 'custom']:
            raw = self._call_openai_compatible(user_prompt)
        elif self.provider == 'anthropic':
            raw = self._call_anthropic(user_prompt)
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")

        return self._parse_response(raw)

    # ---------------------------------------------------------- OpenAI Compatible
    def _call_openai_compatible(self, user_prompt):
        """Call any OpenAI-compatible API (OpenAI, Moonshot, Ollama, etc.)."""
        try:
            import openai
        except ImportError:
            raise ImportError("pip install openai — required for OpenAI-compatible providers")

        client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url)
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=4096,
        )
        return response.choices[0].message.content

    # ---------------------------------------------------------- Anthropic
    def _call_anthropic(self, user_prompt):
        """Call the Anthropic Messages API."""
        try:
            import anthropic
        except ImportError:
            raise ImportError("pip install anthropic  — required for Anthropic provider")

        client = anthropic.Anthropic(api_key=self.api_key)
        response = client.messages.create(
            model=self.model,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            temperature=0.2,
            max_tokens=4096,
        )
        return response.content[0].text

    # ---------------------------------------------------------- parse
    @staticmethod
    def _parse_response(raw_text):
        """Extract the JSON array from the LLM's response text."""
        # Try to find a JSON array in the response
        match = re.search(r'\[.*\]', raw_text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
                # Validate structure
                results = []
                for item in data:
                    results.append({
                        'gate_id': str(item.get('gate_id', '')),
                        'suspicion_score': float(item.get('suspicion_score', 0)),
                        'reason': str(item.get('reason', '')),
                    })
                return sorted(results, key=lambda x: x['suspicion_score'], reverse=True)
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
        return []


# ---------------------------------------------------------- offline fallback
class OfflineLLMAnalyzer:
    """Heuristic-based fallback when no API key is available.
    Uses statistical anomaly detection to identify relatively stealthy gates.
    """

    def analyze_circuit(self, circuit_summary, gate_profiles, past_experiences=None):
        """Score gates using statistical heuristics based on raw structural features."""
        ccs = []
        cos = []
        trs = []
        valid_profiles = []

        for p in gate_profiles:
            f = p['features']
            if f.get('node_type') == 'PI':
                continue
            valid_profiles.append(p)
            ccs.append(f.get('cc0', 0) + f.get('cc1', 0))
            cos.append(f.get('co', 0))
            trs.append(f.get('toggle_rate', 0.5))

        if not valid_profiles:
            return []

        # Calculate means and std devs with simple Python fallback
        n = len(valid_profiles)
        mean_cc = sum(ccs) / n
        std_cc = (sum((x - mean_cc)**2 for x in ccs) / n)**0.5 or 1.0
        mean_co = sum(cos) / n
        std_co = (sum((x - mean_co)**2 for x in cos) / n)**0.5 or 1.0
        mean_tr = sum(trs) / n
        std_tr = (sum((x - mean_tr)**2 for x in trs) / n)**0.5 or 0.1

        results = []
        for p in valid_profiles:
            f = p['features']
            gate_id = p['gate_id']
            score = 0.0
            reasons = []

            cc = f.get('cc0', 0) + f.get('cc1', 0)
            co = f.get('co', 0)
            tr = f.get('toggle_rate', 0.5)
            cent = f.get('centrality', 0)

            # 1. SCOAP Outlier (Low Controllability/Observability)
            if std_cc > 0 and cc > mean_cc + 1.5 * std_cc:
                score += 0.3
                reasons.append(f"Low controllability (CC={cc} vs avg {mean_cc:.1f})")
            if std_co > 0 and co > mean_co + 1.5 * std_co:
                score += 0.3
                reasons.append(f"Low observability (CO={co} vs avg {mean_co:.1f})")

            # 2. Toggle Rate Anomaly (Stealth)
            if tr < 0.01:
                score += 0.4
                reasons.append(f"Extremely rare activity ({tr:.6f})")
            elif std_tr > 0 and tr < mean_tr - 1.2 * std_tr:
                score += 0.2
                reasons.append(f"Below average toggle activity ({tr:.4f} vs avg {mean_tr:.4f})")

            # 3. Centrality / Isolation
            if cent < 1e-4 and f.get('fan_in', 0) > 2:
                score += 0.1
                reasons.append("Structural isolation (Low centrality)")

            score = min(score, 1.0)

            if score >= 0.3:
                results.append({
                    'gate_id': gate_id,
                    'suspicion_score': round(score, 2),
                    'reason': '; '.join(reasons),
                })

        return sorted(results, key=lambda x: x['suspicion_score'], reverse=True)
