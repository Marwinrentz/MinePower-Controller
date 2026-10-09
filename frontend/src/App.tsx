import { useEffect } from "react";
import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { Battery } from "./pages/Battery";
import { Changelog } from "./pages/Changelog";
import { Car } from "./pages/Car";
import { Dashboard } from "./pages/Dashboard";
import { Diagnose } from "./pages/Diagnose";
import { Login } from "./pages/Login";
import { Settings } from "./pages/Settings";
import { Statistics } from "./pages/Statistics";
import { Tariff } from "./pages/Tariff";
import { Wall } from "./pages/Wall";
import { Water } from "./pages/Water";
import { Wizard } from "./pages/Wizard";
import { keepSessionAlive, useAuth } from "./store/auth";

function RequireAuth() {
  const token = useAuth((s) => s.token);
  // Solange jemand angemeldet ist, das Token gleitend verlängern – sonst lief
  // es mitten im Betrieb ab, oft genau dann, wenn man etwas schalten wollte.
  useEffect(() => (token ? keepSessionAlive() : undefined), [token]);
  return token ? <Outlet /> : <Navigate to="/login" replace />;
}

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<RequireAuth />}>
          <Route path="/wizard" element={<Wizard />} />
          {/* Wand-Ansicht ohne Rahmen: ein Wandgerät zeigt nur die Anlage. */}
          <Route path="/wall" element={<Wall />} />
          <Route element={<AppShell />}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/auto" element={<Car />} />
            <Route path="/warmwasser" element={<Water />} />
            <Route path="/batterie" element={<Battery />} />
            <Route path="/tarif" element={<Tariff />} />
            <Route path="/statistik" element={<Statistics />} />
            <Route path="/diagnose" element={<Diagnose />} />
            <Route path="/einstellungen" element={<Settings />} />
            <Route path="/changelog" element={<Changelog />} />
            {/* Alte Adressen (Lesezeichen, Handy-App) weiterleiten */}
            <Route path="/vehicle" element={<Navigate to="/auto" replace />} />
            <Route path="/water" element={<Navigate to="/warmwasser" replace />} />
            <Route path="/devices" element={<Navigate to="/einstellungen?tab=devices" replace />} />
            <Route path="/statistics" element={<Navigate to="/statistik" replace />} />
            <Route path="/settings" element={<Navigate to="/einstellungen" replace />} />
            <Route path="/events" element={<Navigate to="/diagnose?tab=events" replace />} />
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
