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
import Analytics from '@/pages/Analytics';
import Help from '@/pages/Help';
import Admin from '@/pages/Admin';
import NotFound from '@/pages/NotFound';

export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppLayout />,
    errorElement: <NotFound />,
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
      { path: '/analytics', element: <Analytics /> },
      { path: '/help', element: <Help /> },
      { path: '/admin', element: <Admin /> },
      { path: '*', element: <NotFound /> },
    ],
  },
]);
