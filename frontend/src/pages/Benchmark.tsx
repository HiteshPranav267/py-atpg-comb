import { useState, useEffect } from 'react';
import { Rocket, Activity, CheckCircle2, XCircle } from 'lucide-react';
import { useMutation } from '@tanstack/react-query';
import { api } from '@/services/api';
import { DataTable } from '@/components/tables/DataTable';
import { createColumnHelper } from '@tanstack/react-table';
import type { EvaluationResult } from '@/types';

export default function Benchmark() {
  const [datasetDir, setDatasetDir] = useState<string>('tests/fixtures/dataset');
  const [selectedRanker, setSelectedRanker] = useState<string>('random');
  const [k, setK] = useState<number>(10);
  
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<'IDLE' | 'RUNNING' | 'COMPLETED' | 'FAILED'>('IDLE');
  const [progress, setProgress] = useState<number>(0);
  const [results, setResults] = useState<EvaluationResult[]>([]);
  const [errorMsg, setErrorMsg] = useState<string>('');

  const [rankers, setRankers] = useState<string[]>([]);

  useEffect(() => {
    api.getRankers().then(setRankers).catch(console.error);
  }, []);

  const startMutation = useMutation({
    mutationFn: api.startBenchmark,
    onSuccess: (data) => {
      setJobId(data.job_id);
      setStatus('RUNNING');
      setProgress(0);
      setResults([]);
      setErrorMsg('');
    },
    onError: (err: any) => {
      setStatus('FAILED');
      setErrorMsg(err.message || 'Failed to start benchmark');
    }
  });

  useEffect(() => {
    let intervalId: ReturnType<typeof setInterval>;

    if (status === 'RUNNING' && jobId) {
      intervalId = setInterval(async () => {
        try {
          const res = await api.getBenchmarkStatus(jobId);
          setProgress(res.progress);
          if (res.status === 'COMPLETED') {
            setStatus('COMPLETED');
            const resultData = await api.getBenchmarkResult(jobId);
            setResults(resultData.results || []);
          } else if (res.status === 'FAILED') {
            setStatus('FAILED');
            setErrorMsg(res.message);
          }
        } catch (err) {
          console.error('Error polling status:', err);
        }
      }, 2000);
    }

    return () => {
      if (intervalId) clearInterval(intervalId);
    };
  }, [status, jobId]);

  const handleStart = () => {
    startMutation.mutate({
      dataset_dir: datasetDir,
      ranker_name: selectedRanker,
      k
    });
  };

  const columnHelper = createColumnHelper<EvaluationResult>();
  const columns = [
    columnHelper.accessor('circuit_name', { header: 'Circuit' }),
    columnHelper.accessor('top1_accuracy', { header: 'Top-1 Acc' }),
    columnHelper.accessor('top5_accuracy', { header: 'Top-5 Acc' }),
    columnHelper.accessor('top10_accuracy', { header: 'Top-10 Acc' }),
    columnHelper.accessor('recall_at_k', { header: `Recall@${k}` }),
    columnHelper.accessor('precision_at_k', { header: `Precision@${k}` }),
    columnHelper.accessor('mean_reciprocal_rank', { header: 'MRR' }),
    columnHelper.accessor('search_space_reduction', { header: 'SSR' }),
  ];

  return (
    <div className="h-full flex flex-col p-6 max-w-6xl mx-auto w-full gap-6">
      <div className="flex items-center gap-3">
        <Rocket className="w-6 h-6 text-primary" />
        <h2 className="text-2xl font-bold">Benchmark Suite</h2>
      </div>

      <div className="grid grid-cols-3 gap-6">
        <div className="col-span-1 bg-card border border-border p-5 rounded-lg flex flex-col gap-4">
          <h3 className="font-semibold text-sm uppercase tracking-wider text-muted-foreground">Configuration</h3>
          
          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium">Dataset Directory</label>
            <input 
              type="text" 
              value={datasetDir}
              onChange={e => setDatasetDir(e.target.value)}
              className="bg-background border border-border rounded px-3 py-2 text-sm focus:border-primary focus:outline-none font-mono"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium">Ranker</label>
            <select 
              value={selectedRanker}
              onChange={e => setSelectedRanker(e.target.value)}
              className="bg-background border border-border rounded px-3 py-2 text-sm focus:border-primary focus:outline-none"
            >
              {rankers.map(r => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium">Top-K Parameter</label>
            <input 
              type="number" 
              value={k}
              onChange={e => setK(parseInt(e.target.value))}
              className="bg-background border border-border rounded px-3 py-2 text-sm focus:border-primary focus:outline-none"
            />
          </div>

          <button 
            onClick={handleStart}
            disabled={status === 'RUNNING'}
            className="mt-4 flex items-center justify-center gap-2 bg-primary hover:bg-primary/90 text-primary-foreground px-4 py-2.5 rounded-md text-sm font-medium transition-colors disabled:opacity-50"
          >
            {status === 'RUNNING' ? <Activity className="w-4 h-4 animate-spin" /> : <Rocket className="w-4 h-4" />}
            {status === 'RUNNING' ? 'Running Benchmark...' : 'Start Benchmark'}
          </button>
        </div>

        <div className="col-span-2 bg-card border border-border p-5 rounded-lg flex flex-col">
          <h3 className="font-semibold text-sm uppercase tracking-wider text-muted-foreground mb-4">Job Status</h3>
          
          <div className="flex items-center gap-4 mb-6">
            <div className="flex-1 bg-secondary h-4 rounded-full overflow-hidden">
              <div 
                className="bg-primary h-full transition-all duration-300"
                style={{ width: `${progress}%` }}
              />
            </div>
            <span className="font-mono text-sm">{progress}%</span>
          </div>

          <div className="flex items-center gap-2 text-sm font-medium">
            {status === 'RUNNING' && <Activity className="w-5 h-5 text-blue-400 animate-pulse" />}
            {status === 'COMPLETED' && <CheckCircle2 className="w-5 h-5 text-green-500" />}
            {status === 'FAILED' && <XCircle className="w-5 h-5 text-red-500" />}
            {status === 'IDLE' && <div className="w-5 h-5 rounded-full border-2 border-muted-foreground" />}
            
            <span>{status}</span>
            {jobId && <span className="text-muted-foreground ml-2 font-mono text-xs">Job ID: {jobId}</span>}
          </div>

          {errorMsg && (
            <div className="mt-4 p-3 bg-red-500/10 border border-red-500/50 rounded text-red-400 text-sm font-mono">
              {errorMsg}
            </div>
          )}
        </div>
      </div>

      {results.length > 0 && (
        <div className="flex-1 min-h-[300px] flex flex-col">
          <h3 className="font-semibold text-sm uppercase tracking-wider text-muted-foreground mb-2">Results</h3>
          <DataTable columns={columns} data={results} className="flex-1" />
        </div>
      )}
    </div>
  );
}
