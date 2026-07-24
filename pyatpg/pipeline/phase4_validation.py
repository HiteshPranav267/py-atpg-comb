import os
from pyatpg.exporters.atalanta_log_parser import AtlantaLogParser

class Validator:
    """Phase 4: Validation & Security Heatmap"""

    def __init__(self):
        self.log_parser = AtlantaLogParser()

    def cross_reference(self, ai_suspects, fault_details):
        """Cross-reference AI suspects against the detailed Atalanta fault report."""
        trojans = []
        cleared = []
        unknown = []

        fault_map = {}
        for f in fault_details:
            name = f['fault_name']
            if name not in fault_map:
                fault_map[name] = []
            fault_map[name].append(f)

        for gate in ai_suspects:
            faults = fault_map.get(gate, [])
            if not faults:
                unknown.append({
                    'gate_id': gate,
                    'verdict': 'NO_DATA',
                    'reason': 'Gate not found in ATPG fault report',
                })
                continue

            statuses = [f['status'] for f in faults]

            if any(s in ('undetected', 'redundant', 'untestable') for s in statuses):
                trojans.append({
                    'gate_id': gate,
                    'verdict': 'TROJAN_CANDIDATE',
                    'reason': f'AI flagged + ATPG status: {", ".join(statuses)}',
                    'faults': faults,
                })
            else:
                cleared.append({
                    'gate_id': gate,
                    'verdict': 'CLEARED',
                    'reason': f'All faults detected: {", ".join(statuses)}',
                })

        return trojans, cleared, unknown

    def generate_heatmap(self, trojans, cleared, unknown, profiles):
        """Produce a heatmap dictionary for the frontend."""
        heatmap = {}
        
        # Build lookup
        profiles_lookup = {p['gate_id']: p.get('features', {}) for p in profiles}

        for t in trojans:
            gid = t['gate_id']
            feat = profiles_lookup.get(gid, {})
            heatmap[gid] = {
                'verdict': 'TROJAN_CANDIDATE',
                'color': '#ff1744',
                'severity': 'CRITICAL',
                'toggle_rate': feat.get('toggle_rate', None),
                'co': feat.get('co', None),
                'centrality': feat.get('centrality', None),
            }
        for c in cleared:
            gid = c['gate_id']
            feat = profiles_lookup.get(gid, {})
            heatmap[gid] = {
                'verdict': 'CLEARED',
                'color': '#00e676',
                'severity': 'SAFE',
                'toggle_rate': feat.get('toggle_rate', None),
                'co': feat.get('co', None),
                'centrality': feat.get('centrality', None),
            }
        for u in unknown:
            gid = u['gate_id']
            heatmap[gid] = {
                'verdict': 'NO_DATA',
                'color': '#ffab00',
                'severity': 'UNKNOWN',
            }
        return heatmap

    def validate(self, targeted_log, baseline_log, fault_detail_log, ai_suspects, profiles, circuit_stats):
        """Execute the validation phase by reading logs and returning a full report."""
        
        print("[Phase 4] Parsing targeted Atalanta report...")
        targeted_result = self.log_parser.parse_coverage_report(file_path=targeted_log)
        
        baseline_result = None
        comparison = None
        if baseline_log and os.path.exists(baseline_log):
            print("[Phase 4] Parsing baseline Atalanta report...")
            baseline_result = self.log_parser.parse_coverage_report(file_path=baseline_log)
            comparison = self.log_parser.compare(targeted_result, baseline_result)

        fault_details = []
        if fault_detail_log and os.path.exists(fault_detail_log):
            print("[Phase 4] Parsing per-fault detail report...")
            fault_details = self.log_parser.parse_fault_detail_report(file_path=fault_detail_log)
        else:
            print("[Phase 4] No detailed fault report provided. Using coverage summary only.")
            
        print("[Phase 4] Loading AI suspect list...")
        print(f"          {len(ai_suspects)} gates flagged by AI")
        print("[Phase 4] Cross-referencing AI suspects with Oracle results...")
        trojans, cleared, unknown = self.cross_reference(ai_suspects, fault_details)
        heatmap = self.generate_heatmap(trojans, cleared, unknown, profiles)
        
        report = {
            'targeted_coverage': targeted_result,
            'baseline_coverage': baseline_result,
            'comparison': comparison,
            'trojan_candidates': trojans,
            'cleared_gates': cleared,
            'unknown_gates': unknown,
            'heatmap': heatmap,
            'circuit_stats': circuit_stats,
        }
        
        # Strip raw text from the report to keep it lightweight
        for key in ('targeted_coverage', 'baseline_coverage'):
            if report[key] and 'raw_text' in report[key]:
                del report[key]['raw_text']
                
        return report
