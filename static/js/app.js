document.addEventListener('DOMContentLoaded', () => {
    // ══════════════════════════════════════════════════════════
    // 1. SHARED STATE & NAV
    // ══════════════════════════════════════════════════════════
    let socket = io();
    let currentCircuit = null;
    let cy = null;
    let jobStatusTimer = null;
    const FULL_GRAPH_NODE_LIMIT = 800;

    const tabs = document.querySelectorAll('.nav-tab');
    const pages = document.querySelectorAll('.app-page');

    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const target = tab.dataset.page;
            tabs.forEach(t => t.classList.remove('active'));
            pages.forEach(p => p.classList.remove('active'));
            tab.classList.add('active');
            document.getElementById(`page-${target}`).classList.add('active');
            
            if (target === 'terminal' && term) {
                // Ensure terminal fits the new container size
                fitAddon.fit();
                term.focus();
            }
        });
    });

    // ══════════════════════════════════════════════════════════
    // 2. PAGE 2: AGENTIC LOGIC
    // ══════════════════════════════════════════════════════════
    const inputTabs = document.querySelectorAll('.input-tab');
    const inputAreas = document.querySelectorAll('.input-area');

    inputTabs.forEach(tab => {
        tab.addEventListener('click', () => {
            inputTabs.forEach(t => t.classList.remove('active'));
            inputAreas.forEach(a => a.classList.remove('active'));
            tab.classList.add('active');
            document.getElementById(`input-${tab.dataset.type}`).classList.add('active');
        });
    });

    document.getElementById('btn-load').addEventListener('click', async () => {
        const text = document.getElementById('netlist-text').value;
        log('system', 'Parsing netlist and extracting structural metrics...');
        
        try {
            const res = await fetch('/api/circuit/load', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text, name: 'pasted_circuit.v' })
            });
            const data = await res.json();
            if (data.status === 'success') {
                currentCircuit = data.summary;
                renderSummary(data.summary);
                log('system', `Loaded ${data.summary.name}. Gates: ${data.summary.gates}`);
                
                if ((data.summary.nodes || data.summary.gates) <= FULL_GRAPH_NODE_LIMIT) {
                    log('system', 'Initializing circuit visualization...');
                    initVisualizer();
                } else {
                    log('system', `Large circuit detected (${data.summary.nodes || data.summary.gates} nodes). Skipping full graph render.`);
                }
                
                document.getElementById('btn-analyze').classList.remove('hidden');
            } else {
                log('error', data.msg);
            }
        } catch (err) {
            log('error', err);
        }
    });

    document.getElementById('btn-analyze').addEventListener('click', async () => {
        log('system', 'Running Agentic Trojan Analysis (SCOAP + Centrality + LLM)...');
        try {
            const res = await fetch('/api/circuit/analyze', { method: 'POST' });
            const data = await res.json();
            if (data.status === 'success') {
                log('system', `Analysis complete. Found ${data.suspects.length} suspect gates.`);
                renderSuspectsTable(data.suspects);
                
                if (!currentCircuit || (currentCircuit.nodes || currentCircuit.gates) <= FULL_GRAPH_NODE_LIMIT) {
                    initVisualizer();
                } else {
                    log('system', 'Full graph still skipped. Use the suspect list and Modus validation for this large circuit.');
                }
                
                document.getElementById('btn-export').classList.remove('hidden');
                document.getElementById('btn-dispatch').classList.remove('hidden');
                document.getElementById('btn-dispatch').textContent = 'Dispatch Atalanta (Local)';
            } else {
                log('error', data.msg);
            }
        } catch (err) {
            log('error', err);
        }
    });

    document.getElementById('btn-dispatch').addEventListener('click', async () => {
        log('system', 'Packaging Atalanta Job Bundle and running locally...');
        try {
            const res = await fetch('/api/jobs/dispatch', { method: 'POST' });
            const data = await res.json();
            if (data.status === 'success') {
                log('system', `Local directory created: ${data.remote_dir}`);
                log('system', `Progress log: ${data.remote_dir}/targeted_atalanta.log`);
                startJobStatusPolling(data.remote_dir);
            } else {
                log('error', data.msg);
            }
        } catch (err) {
            log('error', err);
        }
    });

    function startJobStatusPolling(remoteDir) {
        if (jobStatusTimer) clearInterval(jobStatusTimer);

        async function poll() {
            try {
                const res = await fetch(`/api/jobs/status?remote_dir=${encodeURIComponent(remoteDir)}`);
                const data = await res.json();
                if (data.status !== 'success') {
                    log('error', data.msg || 'Could not read job status');
                    return;
                }

                if (data.job_status === 'COMPLETED') {
                    log('system', 'Local Atalanta ATPG finished.');
                    clearInterval(jobStatusTimer);
                    jobStatusTimer = null;
                } else if (data.job_status === 'failed') {
                    log('error', `ATPG failed. Inspect ${data.log_hint}`);
                    clearInterval(jobStatusTimer);
                    jobStatusTimer = null;
                }
            } catch (err) {
                log('error', err);
            }
        }

        poll();
        jobStatusTimer = setInterval(poll, 3000);
    }

    // Visit node on clicking suspect table row button
    document.getElementById('suspects-table').addEventListener('click', (e) => {
        const btn = e.target.closest('.btn-visit-node');
        if (btn) {
            const nodeId = btn.dataset.node;
            console.log("Visit node clicked for:", nodeId);
            if (cy) {
                let node = cy.getElementById(nodeId);
                if (node.length === 0) {
                    console.log("Exact match failed, trying fallback search for:", nodeId);
                    const cleanId = nodeId.trim().replace(/^\\/, '');
                    node = cy.nodes().filter(n => {
                        const nid = n.id().trim().replace(/^\\/, '');
                        return nid === cleanId || nid.includes(cleanId) || cleanId.includes(nid);
                    });
                }
                if (node.length > 0) {
                    cy.stop(); // Stop any ongoing layout animations
                    cy.animate({
                        center: { eles: node },
                        zoom: 2.0, // Specific clean zoom level
                        duration: 600
                    });
                    cy.nodes().unselect();
                    node.select();
                    showNodeInfo(node.data());
                    updateZoomIndicator();
                } else {
                    console.warn("Could not find node in Cytoscape. Node ID:", nodeId);
                    log('error', `Node "${nodeId}" not found in the visualization graph.`);
                }
            }
        }
    });

    function log(type, msg) {
        const consoleEl = document.getElementById('live-console');
        const line = document.createElement('div');
        line.className = `log-line ${type}`;
        line.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
        consoleEl.appendChild(line);
        consoleEl.scrollTop = consoleEl.scrollHeight;
    }

    function renderSummary(s) {
        const el = document.getElementById('circuit-summary');
        el.classList.remove('hidden');
        el.innerHTML = `
            <div class="flex justify-between items-center mb-4">
                <div class="flex items-center gap-2">
                    <span class="material-symbols-outlined text-accent-cyan" style="font-size: 1.2rem;">circuit</span>
                    <span style="font-weight: 700; letter-spacing: 0.5px;">${s.name}</span>
                </div>
                <span class="status-badge">READY</span>
            </div>
            <div class="grid grid-cols-3 gap-3 text-center">
                <div class="stat-pill">
                    <div class="stat-val">${s.gates}</div>
                    <div class="stat-lab">Gates</div>
                </div>
                <div class="stat-pill">
                    <div class="stat-val">${s.inputs}</div>
                    <div class="stat-lab">Inputs</div>
                </div>
                <div class="stat-pill">
                    <div class="stat-val">${s.outputs}</div>
                    <div class="stat-lab">Outputs</div>
                </div>
            </div>
            ${s.nodes ? `<div class="text-xs text-dim mt-3">Graph nodes: ${s.nodes}</div>` : ''}
        `;
    }

    function renderSuspectsTable(suspects) {
        const el = document.getElementById('suspects-table');
        el.classList.remove('hidden');
        el.innerHTML = `
            <div class="table-header-mini">Potential Vulnerabilities</div>
            <table class="w-full text-xs text-left border-separate" style="border-spacing: 0 8px;">
                <thead class="text-dim">
                    <tr>
                        <th class="pb-2 pl-2">GATE ID</th>
                        <th class="pb-2">CONFIDENCE</th>
                        <th class="pb-2 text-right pr-2">THREAT</th>
                    </tr>
                </thead>
                <tbody>
                    ${suspects.map(s => `
                        <tr class="table-row-premium">
                            <td class="py-3 pl-3 rounded-l-lg" style="background: rgba(255,255,255,0.02); font-family: var(--font-mono);">${s.gate_id}</td>
                            <td class="py-3" style="background: rgba(255,255,255,0.02);">
                                <div class="flex items-center gap-2">
                                    <div class="w-12 h-1 bg-white/5 rounded-full overflow-hidden">
                                        <div class="h-full bg-accent-cyan" style="width: ${s.score}%"></div>
                                    </div>
                                    <span class="text-accent-cyan font-bold">${s.score}%</span>
                                </div>
                            </td>
                            <td class="py-3 text-right pr-3 rounded-r-lg" style="background: rgba(255,255,255,0.02);">
                                <button class="btn-visit-node badge-${s.score > 70 ? 'danger' : 'warning'}" data-node="${s.gate_id}">
                                    ${s.score > 70 ? 'CRITICAL' : 'SUSPECT'}
                                </button>
                            </td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        `;
    }

    // ══════════════════════════════════════════════════════════
    // 4. VISUALIZER LOGIC (INTEGRATED)
    // ══════════════════════════════════════════════════════════
    async function initVisualizer() {
        const res = await fetch('/api/circuit/graph');
        const elements = await res.json();
        if (elements.status === 'too_large') {
            log('system', elements.msg);
            return;
        }
        if (elements.status === 'error') {
            log('error', elements.msg);
            return;
        }

        cy = cytoscape({
            container: document.getElementById('cy'),
            elements: elements,
            style: [
                {
                    selector: 'node',
                    style: {
                        'label': 'data(id)',
                        'width': 30, 'height': 30,
                        'background-color': '#111424',
                        'border-width': 1, 'border-color': '#00f0ff',
                        'color': '#fff', 'font-size': '8px',
                        'text-valign': 'center', 'text-halign': 'center'
                    }
                },
                {
                    selector: 'node[?is_suspect]',
                    style: {
                        'border-color': '#ff2e63',
                        'border-width': 3,
                        'shadow-blur': 10, 'shadow-color': '#ff2e63', 'shadow-opacity': 0.8
                    }
                },
                {
                    selector: 'edge',
                    style: {
                        'width': 1, 'line-color': 'rgba(255,255,255,0.1)',
                        'target-arrow-color': 'rgba(255,255,255,0.1)',
                        'target-arrow-shape': 'triangle', 'curve-style': 'bezier'
                    }
                }
            ],
            layout: { name: 'dagre', rankDir: 'LR' }
        });

        cy.on('tap', 'node', e => showNodeInfo(e.target.data()));
        cy.on('zoom pan', updateZoomIndicator);
        updateZoomIndicator();
    }

    function showNodeInfo(data) {
        const panel = document.getElementById('node-panel');
        panel.classList.remove('hidden');
        document.getElementById('node-id').textContent = data.id;
        document.getElementById('node-info').innerHTML = `
            <div class="space-y-4">
                <div class="bg-black/30 p-3 rounded">
                    <div class="text-[10px] text-dim">METRICS</div>
                    <div class="grid grid-cols-2 gap-2 mt-2 text-xs">
                        <div>CC0: ${data.cc0 || 0}</div>
                        <div>CC1: ${data.cc1 || 0}</div>
                        <div>CO: ${data.co || 0}</div>
                        <div>Toggle: ${data.toggle || 0}</div>
                    </div>
                </div>
                ${data.is_suspect ? `
                    <div class="bg-pink-900/20 border border-pink-500/30 p-3 rounded">
                        <div class="text-[10px] text-accent font-bold">TROJAN ANALYSIS</div>
                        <div class="text-xs mt-1">Suspicion: ${data.suspicion_score}%</div>
                        <div class="text-[10px] mt-2 text-white/70 italic">"${data.reason}"</div>
                    </div>
                ` : ''}
            </div>
        `;
    }

    document.getElementById('close-node-panel').addEventListener('click', () => {
        document.getElementById('node-panel').classList.add('hidden');
    });

    function updateZoomIndicator() {
        if (cy) {
            const zoomPercent = Math.round(cy.zoom() * 100);
            const indicator = document.getElementById('zoom-indicator');
            if (indicator) {
                indicator.textContent = `${zoomPercent}%`;
            }
        }
    }

    // Zoom & Fit Controls
    document.getElementById('btn-zoom-in').addEventListener('click', () => {
        if (cy) {
            cy.zoom(cy.zoom() * 1.2);
            updateZoomIndicator();
        }
    });
    document.getElementById('btn-zoom-out').addEventListener('click', () => {
        if (cy) {
            cy.zoom(cy.zoom() * 0.8);
            updateZoomIndicator();
        }
    });
    document.getElementById('btn-zoom-fit').addEventListener('click', () => {
        if (cy) {
            cy.fit();
            cy.center();
            updateZoomIndicator();
        }
    });

    // Metric Select Coloring
    document.getElementById('metric-select').addEventListener('change', (e) => {
        const metric = e.target.value;
        if (metric === 'none') {
            cy.nodes().style('background-color', '#111424');
            return;
        }

        const nodes = cy.nodes();
        const values = nodes.map(n => n.data(metric) || 0);
        const max = Math.max(...values, 1);

        nodes.forEach(n => {
            const val = n.data(metric) || 0;
            const ratio = val / max;
            // Green (safe) -> Red (suspect)
            const r = Math.floor(255 * ratio);
            const g = Math.floor(255 * (1 - ratio));
            n.style('background-color', `rgb(${r}, ${g}, 0)`);
        });
    });
});
