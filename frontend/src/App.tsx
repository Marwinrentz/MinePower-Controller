import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { useIsPhone } from "./components/ui";
import { MobileDashboard } from "./mobile/MobileDashboard";
import { MobileShell } from "./mobile/MobileShell";
import { Dashboard } from "./pages/Dashboard";
import { Vehicle } from "./pages/Vehicle";
import { Devices } from "./pages/Devices";
import { Events } from "./pages/Events";
import { Login } from "./pages/Login";
import { Settings } from "./pages/Settings";
import { Statistics } from "./pages/Statistics";
import { Wall } from "./pages/Wall";
import { Wizard } from "./pages/Wizard";
import { useAuth } from "./store/auth";

function RequireAuth() {
  const token = useAuth((s) => s.token);
  return token ? <Outlet /> : <Navigate to="/login" replace />;
}

/** Handy und Desktop bekommen ein eigenes Gerüst.
 *
 *  Kein „responsives Zusammenquetschen": Am Telefon läuft eine eigene
 *  Oberfläche mit eigenem Aufbau, eigener Navigation und eigener
 *  Formensprache (siehe mobile/). Beide teilen sich Routing, Anmeldung und
 *  den Live-Datenstrom – doppelte Logik gäbe es sonst zweimal zu pflegen. */
function Shell() {
  return useIsPhone() ? <MobileShell /> : <Layout />;
}

function Home() {
  return useIsPhone() ? <MobileDashboard /> : <Dashboard />;
}

/** Die übrigen Seiten teilen sich beide Oberflächen. Am Handy bekommen sie
 *  den Scroll-Rahmen der mobilen Oberfläche (Seitenränder, Platz für die
 *  Tab-Leiste), damit nichts unter der Leiste verschwindet. */
function Page({ children }: { children: React.ReactNode }) {
  return useIsPhone() ? <div className="m-scroll m-in">{children}</div> : <>{children}</>;
}

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<RequireAuth />}>
          <Route path="/wizard" element={<Wizard />} />
          {/* Wand-Ansicht bewusst ohne Gerüst: ein Wandgerät zeigt nur die
              Anlage, keine Navigation. */}
          <Route path="/wall" element={<Wall />} />
          <Route element={<Shell />}>
            <Route path="/" element={<Home />} />
            <Route path="/vehicle" element={<Page><Vehicle /></Page>} />
            <Route path="/devices" element={<Page><Devices /></Page>} />
            <Route path="/statistics" element={<Page><Statistics /></Page>} />
            <Route path="/settings" element={<Page><Settings /></Page>} />
            <Route path="/events" element={<Page><Events /></Page>} />
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
