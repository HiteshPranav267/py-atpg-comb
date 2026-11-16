import { useAppStore } from '@/store';
import { Cpu, CheckCircle2, XCircle, Activity } from 'lucide-react';
import { cn } from '@/utils/utils';

export function StatusBar() {
  const { activeCircuit, activeRanking, backendStatus } = useAppStore();

  return (
    <footer className="h-8 border-t border-border bg-card flex items-center justify-between px-4 text-[11px] font-mono text-muted-foreground select-none">
      <div className="flex items-center gap-6">
        <div className="flex items-center gap-1.5">
          <Cpu className="w-3.5 h-3.5" />
          <span>{activeCircuit ? activeCircuit.name : 'No Circuit Loaded'}</span>
        </div>
        {activeCircuit && (
          <div className="flex items-center gap-4 text-muted-foreground/80">
            <span>Gates: {activeCircuit.gates}</span>
            <span>I/O: {activeCircuit.primary_inputs}/{activeCircuit.primary_outputs}</span>
            <span>Levels: {activeCircuit.levels}</span>
          </div>
        )}
      </div>

      <div className="flex items-center gap-6">
        {activeRanking && (
          <div className="flex items-center gap-1.5 text-accent-foreground">
            <span>Ranker: {activeRanking.ranker_name}</span>
          </div>
        )}
        
        <div className="flex items-center gap-1.5">
          {backendStatus === 'CONNECTED' && <CheckCircle2 className="w-3.5 h-3.5 text-green-500" />}
          {backendStatus === 'DISCONNECTED' && <XCircle className="w-3.5 h-3.5 text-red-500" />}
          {backendStatus === 'BUSY' && <Activity className="w-3.5 h-3.5 text-orange-400 animate-pulse" />}
          
          <span className={cn(
            backendStatus === 'CONNECTED' && "text-green-500",
            backendStatus === 'DISCONNECTED' && "text-red-500",
            backendStatus === 'BUSY' && "text-orange-400"
          )}>
            Backend: {backendStatus}
          </span>
        </div>
      </div>
    </footer>
  );
}
