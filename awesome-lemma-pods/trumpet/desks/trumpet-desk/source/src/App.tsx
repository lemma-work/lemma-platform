import * as React from 'react';
import { HashRouter } from 'react-router-dom';
import { AnimatePresence, motion } from 'framer-motion';
import { AuthGuard } from 'lemma-sdk/react';
import { MrTootChat } from '@/components/trumpet/chat/MrTootChat';
import { Stage }    from '@/components/trumpet/layout/Stage';
import { Dock, TAB_ORDER } from '@/components/trumpet/layout/Dock';
import type { Tab } from '@/components/trumpet/layout/Dock';
import { HomeTab }     from '@/components/trumpet/home/HomeTab';
import { CommitsTab }  from '@/components/trumpet/commits/CommitsTab';
import { ScheduleTab } from '@/components/trumpet/schedule/ScheduleTab';
import { NotesTab }    from '@/components/trumpet/notes/NotesTab';
import { YouTab }      from '@/components/trumpet/you/YouTab';
import { client }   from '@/lib/client';
import { tokens }   from '@/lib/tokens';
import { useCommitments } from '@/hooks/useCommitments';
import { SplashScreen } from '@/components/trumpet/splash/SplashScreen';

// ── Tab transition variants ──────────────────────────────────────────────────
const tabVariants = {
  enter:  (dir: number) => ({ x: dir > 0 ?  1512 : -1512, opacity: 0 }),
  center: {
    x: 0, opacity: 1,
    transition: { duration: 0.28, ease: [0.25, 0.46, 0.45, 0.94] as const },
  },
  exit: (dir: number) => ({
    x: dir > 0 ? -1512 : 1512, opacity: 0,
    transition: { duration: 0.22, ease: [0.55, 0.06, 0.68, 0.19] as const },
  }),
};

export default function App() {
  const [splashDone, setSplashDone] = React.useState(
    () => sessionStorage.getItem('trumpet-splash-done') === '1'
  );

  const handleSplashDone = React.useCallback(() => {
    sessionStorage.setItem('trumpet-splash-done', '1');
    setSplashDone(true);
  }, []);

  return (
    <>
      {!splashDone && <SplashScreen onDone={handleSplashDone} />}
      <HashRouter>
        <AuthGuard client={client} loadingFallback={<BootFallback />}>
          <TrumpetShell />
        </AuthGuard>
      </HashRouter>
    </>
  );
}

function TrumpetShell() {
  const [activeTab,    setActiveTab]    = React.useState<Tab>('home');
  const [tabDirection, setTabDirection] = React.useState(1);
  const { all: allCommitments } = useCommitments();
  const overdueCount = allCommitments.filter(c => c.urgency === 'red').length;

  const navigateTo = React.useCallback((tab: Tab) => {
    const dir = TAB_ORDER.indexOf(tab) > TAB_ORDER.indexOf(activeTab) ? 1 : -1;
    setTabDirection(dir);
    setActiveTab(tab);
  }, [activeTab]);

  return (
    <>
      <Stage>
        {/* Tab content — directional slide via Framer Motion */}
        <AnimatePresence mode="wait" custom={tabDirection}>
          <motion.div
            key={activeTab}
            custom={tabDirection}
            variants={tabVariants}
            initial="enter"
            animate="center"
            exit="exit"
            style={{ position: 'absolute', inset: 0 }}
          >
            {activeTab === 'home'     && <HomeTab onNavigate={navigateTo} />}
            {activeTab === 'commits'  && <CommitsTab />}
            {activeTab === 'schedule' && <ScheduleTab />}
            {activeTab === 'notes'    && <NotesTab />}
            {activeTab === 'you'      && <YouTab />}
          </motion.div>
        </AnimatePresence>

        {/* Dock — persistent, layered above tab content */}
        <Dock activeTab={activeTab} onNavigate={navigateTo} commitAlerts={overdueCount} />
      </Stage>

      {/* Mr Toot chat — rendered outside Stage so fixed positioning is viewport-relative */}
      <MrTootChat />
    </>
  );
}

function ComingSoon({ tab }: { tab: Tab }) {
  const labels: Record<Tab, string> = {
    home: 'Home', commits: 'Commits', schedule: 'Schedule', notes: 'Notes', you: 'You',
  };
  return (
    <div style={{
      position:       'absolute',
      inset:          0,
      display:        'flex',
      flexDirection:  'column',
      alignItems:     'center',
      justifyContent: 'center',
      gap:            16,
      fontFamily:     tokens.font,
    }}>
      <p style={{ fontSize: 48, fontWeight: 700, color: tokens.fg, margin: 0, letterSpacing: -1 }}>
        {labels[tab]}
      </p>
      <p style={{ fontSize: 22, color: tokens.muted, margin: 0 }}>
        Coming in the next phase
      </p>
    </div>
  );
}

function BootFallback() {
  return (
    <div style={{
      position:       'fixed',
      inset:          0,
      display:        'flex',
      alignItems:     'center',
      justifyContent: 'center',
      background:     tokens.bg,
      fontFamily:     tokens.font,
      fontSize:       18,
      color:          tokens.muted,
    }}>
      Loading Trumpet…
    </div>
  );
}
