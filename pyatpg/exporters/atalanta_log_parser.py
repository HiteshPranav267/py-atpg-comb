import os
import re

class AtlantaLogParser:
    """Parses output logs from Atalanta ATPG"""
    
    def parse_coverage_report(self, file_path):
        if not os.path.exists(file_path):
            return {'error': 'File not found', 'coverage_pct': 0.0}
            
        with open(file_path, 'r') as f:
            content = f.read()
            
        coverage = 0.0
        patterns = 0
        
        # Extract Fault Coverage
        match_cov = re.search(r'Fault [Cc]overage\s*[:=]\s*([\d\.]+)\s*%', content)
        if match_cov:
            coverage = float(match_cov.group(1))
            
        # Extract Pattern Count
        match_pat = re.search(r'test patterns after compaction\s*[:=]\s*(\d+)', content, re.IGNORECASE)
        if match_pat:
            patterns = int(match_pat.group(1))
        else:
            match_pat = re.search(r'test patterns\s*[:=]\s*(\d+)', content, re.IGNORECASE)
            if match_pat:
                patterns = int(match_pat.group(1))
            
        return {
            'coverage_pct': coverage,
            'test_patterns': patterns,
            'raw_text': content
        }
        
    def parse_fault_detail_report(self, file_path):
        """Parse Atalanta .ufaults file to see which targeted faults were undetected."""
        details = []
        if not os.path.exists(file_path):
            return details
            
        with open(file_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                gate_id = line.split()[0]
                fault_type = line.split()[1] if len(line.split()) > 1 else 'unknown'
                
                details.append({
                    'fault_name': gate_id,
                    'fault_type': fault_type,
                    'status': 'undetected'
                })
                
        return details
        
    def compare(self, targeted, baseline):
        if not targeted or not baseline:
            return {}
            
        t_cov = targeted.get('coverage_pct', 0)
        b_cov = baseline.get('coverage_pct', 0)
        t_pat = targeted.get('test_patterns', 0)
        b_pat = baseline.get('test_patterns', 0)
        
        pat_red = 0
        if b_pat > 0:
            pat_red = round(((b_pat - t_pat) / b_pat) * 100, 2)
            
        return {
            'targeted_coverage': t_cov,
            'baseline_coverage': b_cov,
            'coverage_diff': round(t_cov - b_cov, 2),
            'targeted_patterns': t_pat,
            'baseline_patterns': b_pat,
            'pattern_reduction_pct': pat_red,
            'speedup_factor': round(b_pat / t_pat, 2) if t_pat > 0 else 0
        }
