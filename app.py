import os
import json
import time
import subprocess
from flask import Flask, render_template, request, jsonify, session
from flask_socketio import SocketIO, emit
from werkzeug.utils import secure_filename

from pyatpg.core.netlist import NetlistParser
from pyatpg.core.verilog_parser import VerilogParser
from pyatpg.core.graph_analysis import CircuitGraph
from pyatpg.agents.llm_analyzer import LLMAnalyzer
from pyatpg.core.memory import CircuitMemory

app = Flask(__name__)
app.config['SECRET_KEY'] = 'pyatpg-secret-2025'
socketio = SocketIO(app, cors_allowed_origins="*")

circuit_memory = CircuitMemory()

# Circuit State
state = {
    'circuit': None,
    'graph': None,
    'name': None,
    'profiles': None,
    'suspects': None,
    'raw_text': None,
    'local_dir': None,
    'is_combinational': True,
}

@app.route('/')
def index():
    return render_template('index.html')

# ──────────────────────── PAGE 2: AGENTIC ────────────────────────

@app.route('/api/circuit/load', methods=['POST'])
def load_circuit():
    try:
        if 'file' in request.files:
            f = request.files['file']
            text = f.read().decode('utf-8')
            name = f.filename
        else:
            data = request.json
            text = data.get('text')
            name = data.get('name', 'pasted_circuit')

        fmt = 'verilog' if name.endswith(('.v', '.verilog')) else 'bench'
        parser = VerilogParser() if fmt == 'verilog' else NetlistParser()
        
        circuit = parser.parse(text)
        cg = CircuitGraph(circuit)
        
        state['circuit'] = circuit
        state['graph'] = cg
        state['name'] = name
        state['raw_text'] = text
        state['profiles'] = cg.to_llm_feature_profiles()
        state['is_combinational'] = len(circuit.flipflops) == 0

        stats = cg.get_stats()
        if not state['is_combinational']:
            from pyatpg.seq.timeframe import is_combinational_part_acyclic
            if not is_combinational_part_acyclic(circuit):
                return jsonify({
                    'status': 'error',
                    'msg': 'This circuit has a combinational feedback loop not broken by a '
                           'flip-flop; sequential ATPG here assumes every feedback path passes '
                           'through a clocked flip-flop.',
                }), 400

        return jsonify({
            'status': 'success',
            'summary': {
                'name': name,
                'nodes': stats['total_nodes'],
                'gates': stats['gates'],
                'inputs': stats['primary_inputs'],
                'outputs': stats['primary_outputs'],
                'is_combinational': state['is_combinational'],
                'atpg_backend': 'atalanta' if state['is_combinational'] else 'pyatpg-seq',
                'flip_flops': stats.get('flip_flops', 0),
            }
        })
    except Exception as e:
        return jsonify({'status': 'error', 'msg': str(e)}), 500

@app.route('/api/circuit/analyze', methods=['POST'])
def analyze_circuit():
    if not state['graph']:
        return jsonify({'status': 'error', 'msg': 'No circuit loaded'}), 400
    
    try:
        # 1. Check Memory (Identified Quickly)
        match = circuit_memory.find_match(state['raw_text'])
        if match:
            print(f"[Memory] Found exact match for {state['name']}. Skipping LLM.")
            state['suspects'] = match['results']
            return jsonify({
                'status': 'success',
                'suspects': state['suspects'],
                'cached': True
            })

        # 2. Analysis
        # Use Offline analyzer if no key
        if not os.environ.get('OPENAI_API_KEY') and not os.environ.get('ANTHROPIC_API_KEY'):
            from pyatpg.agents.llm_analyzer import OfflineLLMAnalyzer
            analyzer = OfflineLLMAnalyzer()
        else:
            analyzer = LLMAnalyzer()
            
        summary = state['graph'].generate_circuit_summary_for_llm()
        profiles = state['profiles']
        past_exp = circuit_memory.get_all_summaries()
        
        suspects = analyzer.analyze_circuit(summary, profiles, past_experiences=past_exp)
        
        # Convert 0-1 score to 0-100 percentage for frontend
        for s in suspects:
            s['score'] = int(s['suspicion_score'] * 100)
            
        state['suspects'] = suspects
        
        # 3. Store in Memory
        stats = state['graph'].get_stats()
        stats['name'] = state['name']
        circuit_memory.remember(state['raw_text'], suspects, stats)
        
        return jsonify({
            'status': 'success',
            'suspects': suspects,
            'cached': False
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'msg': str(e)}), 500

@app.route('/api/jobs/dispatch', methods=['POST'])
def dispatch_job():
    if not state['name'] or not state['raw_text']:
        return jsonify({'status': 'error', 'msg': 'No circuit loaded. Please load a circuit first.'}), 400

    try:
        circuit_name = state['name'].split('.')[0]
        if circuit_name.endswith('_mod'):
            circuit_name = circuit_name[:-4]
            
        is_combinational = state['is_combinational']

        def run_in_background():
            if is_combinational:
                from pyatpg.atpg import AtalantaRunner, JobWorkspace, build_fault_lines

                try:
                    workspace = JobWorkspace.create(circuit_name)
                    state['local_dir'] = str(workspace.directory)
                    gates = [s['gate_id'] for s in (state['suspects'] or [])]
                    runner = AtalantaRunner()
                    run = runner.run(
                        workspace,
                        state['circuit'].to_bench(),
                        label='ATPG Tool',
                        targeted_faults=build_fault_lines(gates) if gates else None,
                        targeted_gates=gates,
                    )
                    coverage = run.results.fault_coverage
                    socketio.emit(
                        'terminal_output',
                        f"\n[PyATPG] ATPG tool finished. Fault coverage: "
                        f"{'not reported' if coverage is None else f'{coverage} %'}\n",
                    )
                except Exception as e:
                    socketio.emit('terminal_output', f'\n[PyATPG] Job Failed: {str(e)}\n')
            else:
                from pyatpg.seq.service import SequentialAtpgService

                try:
                    socketio.emit(
                        'terminal_output',
                        '\n[PyATPG] Sequential circuit: running the time-frame-expansion '
                        'ATPG engine.\n',
                    )
                    service = SequentialAtpgService()
                    result = service.run(
                        source_text=state['raw_text'],
                        filename=state['name'],
                        use_llm=False,
                    )
                    r = result.analysis.results
                    socketio.emit(
                        'terminal_output',
                        f"\n[PyATPG] Sequential ATPG finished. Fault coverage: "
                        f"{'not reported' if r.fault_coverage is None else f'{r.fault_coverage} %'} "
                        f"({r.detected_faults}/{r.total_faults} faults, "
                        f"{r.sequence_count} sequence(s), {r.total_clock_cycles} clock cycles).\n",
                    )
                except Exception as e:
                    socketio.emit('terminal_output', f'\n[PyATPG] Job Failed: {str(e)}\n')

        socketio.start_background_task(run_in_background)

        return jsonify({
            'status': 'success',
            'msg': 'Job dispatched. ATPG tool is running locally.' if is_combinational
                   else 'Job dispatched. Sequential ATPG engine is running locally.'
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'msg': str(e)}), 500

@app.route('/api/jobs/status')
def job_status():
    local_dir = request.args.get('remote_dir') or state.get('local_dir')
    if not local_dir:
        return jsonify({'status': 'error', 'msg': 'No job has been dispatched'}), 400

    log_file = os.path.join(local_dir, 'targeted_atalanta.log')
    if os.path.exists(log_file):
        status = "COMPLETED"
    else:
        status = "RUNNING"

    return jsonify({
        'status': 'success',
        'job_status': status,
        'remote_dir': local_dir,
        'log_hint': log_file,
    })

# ──────────────────────── PAGE 3: VISUALIZER ────────────────────────

@app.route('/api/circuit/graph')
def get_graph():
    if not state['graph']:
        return jsonify({'status': 'error', 'msg': 'No circuit loaded'}), 400

    stats = state['graph'].get_stats()
    force = request.args.get('force') == '1'
    if stats['total_nodes'] > 800 and not force:
        return jsonify({
            'status': 'too_large',
            'msg': 'Circuit is too large for full graph rendering. Use suspect tables or force=1.',
            'nodes': stats['total_nodes'],
            'gates': stats['gates'],
        }), 413
    
    graph_data = state['graph'].to_cytoscape_json()
    
    # Merge suspicion scores if available
    if state['suspects']:
        suspect_map = {s['gate_id']: s for s in state['suspects']}
        for node in graph_data['nodes']:
            gid = node['data']['id']
            if gid in suspect_map:
                node['data']['suspicion_score'] = suspect_map[gid]['score']
                node['data']['is_suspect'] = True
                node['data']['reason'] = suspect_map[gid]['reason']
            else:
                node['data']['is_suspect'] = False
                node['data']['suspicion_score'] = 0

    return jsonify(graph_data)

if __name__ == '__main__':
    socketio.run(app, debug=True, port=5000)

