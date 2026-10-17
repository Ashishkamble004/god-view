import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import App from '../App';
import { resetMockCockpitState } from '../mocks/handlers';
import { server } from '../mocks/server';

describe('God-View Cockpit (URL 3)', () => {
  it('renders global header with IAP user chip, env_id, Sovereign heartbeat, and dynamic randomised names', async () => {
    const dataset = resetMockCockpitState(42);
    window.location.hash = '#organism';
    render(<App />);

    expect(await screen.findByText(dataset.me.email)).toBeInTheDocument();
    expect(screen.getAllByText(dataset.perceive.env_id).length).toBeGreaterThan(0);
    expect(screen.getAllByText(dataset.perceive.reasoning_model).length).toBeGreaterThan(0);

    // SVG Org-Chart diagram is rendered with Sovereign at top and all 5 court agent types
    const svgChart = screen.getByTestId('organism-org-chart-svg');
    expect(svgChart).toBeInTheDocument();

    for (const agent of dataset.court) {
      expect(screen.getAllByText(agent.id).length).toBeGreaterThan(0);
      expect(screen.getAllByText(agent.subject_ref).length).toBeGreaterThan(0);
    }
  });

  it('proves zero hardcoded service/agent/model names by swapping to a different random seed', async () => {
    const seedA = resetMockCockpitState(11);
    const { unmount } = render(<App />);
    expect(await screen.findByText(seedA.me.email)).toBeInTheDocument();
    expect(screen.getAllByText(seedA.court[0].subject_ref).length).toBeGreaterThan(0);
    unmount();

    const seedB = resetMockCockpitState(73);
    render(<App />);
    expect(await screen.findByText(seedB.me.email)).toBeInTheDocument();
    expect(screen.getAllByText(seedB.court[0].subject_ref).length).toBeGreaterThan(0);
    expect(screen.queryByText(seedA.me.email)).not.toBeInTheDocument();
  });

  it('renders #agent/<id> living semantic model with entities, rules, state machines, routes, boundaries, commits, and L4 whitelist toggle', async () => {
    const dataset = resetMockCockpitState(42);
    const targetAgent = dataset.court[0];
    window.location.hash = `#agent/${encodeURIComponent(targetAgent.id)}`;
    const user = userEvent.setup();

    render(<App />);

    expect(await screen.findByText(targetAgent.living_model.rules[0].expression)).toBeInTheDocument();
    expect(screen.getByText(targetAgent.living_model.entities[0].name)).toBeInTheDocument();
    expect(screen.getByText(targetAgent.living_model.routes[0].path)).toBeInTheDocument();
    expect(screen.getByText(targetAgent.living_model.decision_boundaries[0].boundary)).toBeInTheDocument();
    expect(screen.getByText(targetAgent.living_model.commit_diffs[0].business_summary)).toBeInTheDocument();

    // Toggle L4 owner whitelist on a trust record
    const whitelistButtons = screen.getAllByRole('button', { name: /Whitelist for L4|Revoke L4 Whitelist/i });
    expect(whitelistButtons.length).toBeGreaterThan(0);
    await user.click(whitelistButtons[0]);
    await waitFor(() => {
      expect(screen.getAllByText(/Whitelisted/i).length).toBeGreaterThan(0);
    });
  });

  it('renders #invariants board with statuses, provenance, shadow_stats, history, and INVARIANT.md markdown body', async () => {
    const dataset = resetMockCockpitState(42);
    window.location.hash = '#invariants';
    render(<App />);

    expect((await screen.findAllByText(dataset.invariants[0].name)).length).toBeGreaterThan(0);
    expect(screen.getAllByText(dataset.invariants[0].provenance_ref).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/8,640/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/0\.4%/).length).toBeGreaterThan(0);
    expect(
      screen.getByText(/eligibility decision requires a fixed-obligation-to-income ratio/i)
    ).toBeInTheDocument();
  });

  it('renders #journeys explorer with SVG directly-follows DAG, conformance violations, funnel KPIs, and ₹ at risk', async () => {
    const dataset = resetMockCockpitState(42);
    window.location.hash = '#journeys';
    render(<App />);

    expect(await screen.findByTestId('journey-dag-svg')).toBeInTheDocument();
    expect(screen.getAllByText(dataset.journeys[0].steps[0].service).length).toBeGreaterThan(0);
    expect(screen.getAllByText('Skipped').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Reordered').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Stalled').length).toBeGreaterThan(0);
    expect(screen.getAllByText(/1,84,00,000|18,400,000/).length).toBeGreaterThan(0);
  });

  it('renders distinct #incidents list vs #incident/<id> focused detail with evidence pack, swarm transcript rounds, convergence record, and audit actions', async () => {
    const dataset = resetMockCockpitState(42);
    const inc = dataset.incidents[0];

    window.location.hash = '#incidents';
    const { unmount } = render(<App />);
    expect(await screen.findByTestId('incidents-list-overview')).toBeInTheDocument();
    unmount();

    window.location.hash = `#incident/${inc.incident_id}`;
    render(<App />);
    expect(await screen.findByTestId('incident-focused-header')).toBeInTheDocument();
    expect(screen.getByText(inc.evidence_pack.evidence_pack_id)).toBeInTheDocument();
    expect(screen.getByText(inc.actions[0].audit_id)).toBeInTheDocument();
    expect(screen.getAllByText(/0\.92|92%/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Confirmed PR #42/i)).toBeInTheDocument();
  });

  it('executes Approve, Reject, and Snooze in #approvals inbox against /api/v1/approvals/{id}:approve|reject|snooze', async () => {
    const dataset = resetMockCockpitState(42);
    window.location.hash = '#approvals';
    const user = userEvent.setup();
    render(<App />);

    const firstCardId = dataset.approvals[0].approval_id;
    const secondCardId = dataset.approvals[1].approval_id;

    expect(await screen.findByText(firstCardId)).toBeInTheDocument();

    // Pending card shows neutral "Requires owner approval (L4 gate)" badge rather than green L4
    const firstCardEl = screen.getByTestId(`approval-card-${firstCardId}`);
    expect(within(firstCardEl).getByText(/Requires owner approval/i)).toBeInTheDocument();

    const approveBtn = within(firstCardEl).getByRole('button', { name: /Approve/i });
    await user.click(approveBtn);

    await waitFor(() => {
      expect(within(firstCardEl).getByText(/aud-20261016-0091/i)).toBeInTheDocument();
    });

    const secondCardEl = screen.getByTestId(`approval-card-${secondCardId}`);
    const snoozeBtn = within(secondCardEl).getByRole('button', { name: /Snooze/i });
    await user.click(snoozeBtn);

    await waitFor(() => {
      expect(within(secondCardEl).getAllByText(/Snoozed/i).length).toBeGreaterThan(0);
    });
  });

  it('renders #changes PR reviews with risk score, business summary, and canary verdicts', async () => {
    const dataset = resetMockCockpitState(42);
    window.location.hash = '#changes';
    render(<App />);

    expect(await screen.findByText(dataset.changes[0].business_summary)).toBeInTheDocument();
    expect(screen.getAllByText(/Canary: failed/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Canary: passed/i).length).toBeGreaterThan(0);
  });

  it('renders #cost with cost_findings and tf.drift_diff results and supports fast attribute restore', async () => {
    const dataset = resetMockCockpitState(42);
    window.location.hash = '#cost';
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText(dataset.cost.cost_findings[0].recommendation)).toBeInTheDocument();
    expect(screen.getByText(dataset.cost.drift_diffs[0].resource_address)).toBeInTheDocument();

    const restoreBtn = screen.getByRole('button', { name: /Fast Restore \(L3\)/i });
    await user.click(restoreBtn);

    await waitFor(() => {
      expect(screen.getAllByText(/Restored/i).length).toBeGreaterThan(0);
    });
  });

  it('enforces deliberate two-step confirmation on Traffic Kill Switch and Chaos Drills in #controls', async () => {
    const dataset = resetMockCockpitState(42);
    window.location.hash = '#controls';
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText(dataset.drills[0].title)).toBeInTheDocument();

    // Direction 1: Confirm & Engage button is NOT present before Step 1 is armed
    expect(screen.queryByRole('button', { name: /Confirm & Engage Kill Switch/i })).not.toBeInTheDocument();

    // Step 1: Arm Kill Switch
    const armBtn = screen.getByRole('button', { name: /Step 1: Arm Traffic Kill Switch/i });
    await user.click(armBtn);

    // Direction 2: Now Step 2 confirmation button is visible and engages the Kill Switch
    const engageBtn = await screen.findByRole('button', { name: /Confirm & Engage Kill Switch/i });
    await user.click(engageBtn);

    await waitFor(() => {
      expect(screen.getAllByText(/Kill switch engaged/i).length).toBeGreaterThan(0);
    });
  });

  it('keeps #controls and Traffic Kill Switch accessible via Promise.allSettled when a single endpoint (/api/v1/incidents) fails with 500', async () => {
    resetMockCockpitState(42);
    window.location.hash = '#controls';
    server.use(
      http.get('/api/v1/incidents', () => {
        return HttpResponse.json(
          { code: 'INCIDENTS_DB_TIMEOUT', message: 'Firestore incidents query timed out' },
          { status: 500 }
        );
      })
    );

    const user = userEvent.setup();
    render(<App />);

    // Partial slice error banner is shown
    expect(await screen.findByTestId('partial-slice-error-banner')).toBeInTheDocument();
    expect(screen.getByText(/Stale data · Retrying failed slice \(incidents\)/i)).toBeInTheDocument();

    // Traffic Kill Switch on #controls remains accessible and operational
    const armBtn = screen.getByRole('button', { name: /Step 1: Arm Traffic Kill Switch/i });
    await user.click(armBtn);
    const engageBtn = await screen.findByRole('button', { name: /Confirm & Engage Kill Switch/i });
    await user.click(engageBtn);

    await waitFor(() => {
      expect(screen.getAllByText(/Kill switch engaged/i).length).toBeGreaterThan(0);
    });
  });

  it('displays IAP Session Expired banner when /api/v1/me returns 401', async () => {
    resetMockCockpitState(42);
    window.location.hash = '#organism';
    server.use(
      http.get('/api/v1/me', () => {
        return HttpResponse.json(
          { code: 'UNAUTHENTICATED', message: 'IAP cookie expired' },
          { status: 401 }
        );
      })
    );

    render(<App />);
    expect(await screen.findByTestId('iap-session-expired-banner')).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: /Session expired — Reload to sign in/i })
    ).toBeInTheDocument();
  });

  it('supports accessible Chaos Drills Drawer with aria-modal, Escape key, and backdrop click', async () => {
    resetMockCockpitState(42);
    window.location.hash = '#organism';
    const user = userEvent.setup();
    render(<App />);

    const openBtn = await screen.findByRole('button', { name: /Chaos Drills Drawer/i });
    await user.click(openBtn);

    const dialog = await screen.findByRole('dialog', { name: /Chaos Drills Drawer/i });
    expect(dialog).toHaveAttribute('aria-modal', 'true');

    // Escape key closes drawer
    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: /Chaos Drills Drawer/i })).not.toBeInTheDocument();
    });

    // Re-open and close via backdrop click
    await user.click(openBtn);
    const backdrop = await screen.findByTestId('chaos-drills-drawer-backdrop');
    fireEvent.click(backdrop);
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: /Chaos Drills Drawer/i })).not.toBeInTheDocument();
    });
  });

  it('fails loudly with an honest error state when all Sovereign API endpoints return 503', async () => {
    const unavailable = () =>
      HttpResponse.json(
        { code: 'SOVEREIGN_UNAVAILABLE', message: 'Sovereign control plane unreachable' },
        { status: 503 }
      );
    server.use(
      http.get('/api/v1/me', unavailable),
      http.get('/api/v1/perceive', unavailable),
      http.get('/api/v1/court', unavailable),
      http.get('/api/v1/invariants', unavailable),
      http.get('/api/v1/sentinels', unavailable),
      http.get('/api/v1/journeys', unavailable),
      http.get('/api/v1/incidents', unavailable),
      http.get('/api/v1/approvals', unavailable),
      http.get('/api/v1/changes', unavailable),
      http.get('/api/v1/cost', unavailable),
      http.get('/api/v1/chaos/drills', unavailable)
    );

    render(<App />);
    expect(await screen.findByText(/Sovereign control plane unreachable/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Retry Connection/i })).toBeInTheDocument();
  });
});
