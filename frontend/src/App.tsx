/**
 * CQR App — Root component with routing.
 * All 11 screens routed through the persistent AppShell.
 */

import React, { useEffect } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { AppShell } from './components/Layout/AppShell';
import { useSessionStore } from './stores';
import { api } from './api/client';

import { ProjectHub }      from './screens/ProjectHub/ProjectHub';
import { Onboarding }      from './screens/Onboarding/Onboarding';
import { IDEMain }         from './screens/IDE/IDEMain';
import { KGExplorer }      from './screens/KGExplorer/KGExplorer';
import { LSMView }         from './screens/LSMView/LSMView';
import { SecurityScanner } from './screens/SecurityScanner/SecurityScanner';
import { DeployGate }      from './screens/DeployGate/DeployGate';
import { Vault }           from './screens/Vault/Vault';
import { Sandbox }         from './screens/Sandbox/Sandbox';
import { Connectors }      from './screens/Connectors/Connectors';
import { Settings }        from './screens/Settings/Settings';

import './styles/global.css';
import './styles/components.css';

const BackendHealthMonitor: React.FC = () => {
  const { setBackendOnline } = useSessionStore();
  useEffect(() => {
    const check = async () => {
      const ok = await api.health.check();
      setBackendOnline(ok);
    };
    check();
    const interval = setInterval(check, 10_000);
    return () => clearInterval(interval);
  }, [setBackendOnline]);
  return null;
};

const App: React.FC = () => (
  <BrowserRouter>
    <BackendHealthMonitor />
    <AppShell>
      <Routes>
        <Route path="/"           element={<ProjectHub />} />
        <Route path="/onboarding" element={<Onboarding />} />
        <Route path="/ide"        element={<IDEMain />} />
        <Route path="/kg"         element={<KGExplorer />} />
        <Route path="/lsm"        element={<LSMView />} />
        <Route path="/security"   element={<SecurityScanner />} />
        <Route path="/deploy"     element={<DeployGate />} />
        <Route path="/vault"      element={<Vault />} />
        <Route path="/sandbox"    element={<Sandbox />} />
        <Route path="/connectors" element={<Connectors />} />
        <Route path="/settings"   element={<Settings />} />
      </Routes>
    </AppShell>
  </BrowserRouter>
);

export default App;
