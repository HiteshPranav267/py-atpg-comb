import { createBrowserRouter } from 'react-router-dom';
import { AppLayout } from '@/components/layout/AppLayout';

import Dashboard from '@/pages/Dashboard';
import Circuits from '@/pages/Circuits';
import TrojanDataset from '@/pages/TrojanDataset';
import FeatureAnalysis from '@/pages/FeatureAnalysis';
import ATPG from '@/pages/ATPG';
import Ranking from '@/pages/Ranking';
import Benchmark from '@/pages/Benchmark';
import Results from '@/pages/Results';
import Reports from '@/pages/Reports';
import Help from '@/pages/Help';

export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { path: '/', element: <Dashboard /> },
      { path: '/circuits', element: <Circuits /> },
      { path: '/trojan', element: <TrojanDataset /> },
      { path: '/features', element: <FeatureAnalysis /> },
      { path: '/atpg', element: <ATPG /> },
      { path: '/ranking', element: <Ranking /> },
      { path: '/benchmark', element: <Benchmark /> },
      { path: '/results', element: <Results /> },
      { path: '/reports', element: <Reports /> },
      { path: '/help', element: <Help /> },
    ],
  },
]);
