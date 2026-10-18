import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import App from '../App';
import { resetMockCockpitState } from '../mocks/handlers';

describe('God-View 3D Globe Cockpit', () => {
  it('renders #globe view by default with left rail, tier summary, and active agents', async () => {
    const dataset = resetMockCockpitState(42);
    window.location.hash = '#globe';
    render(<App />);

    // Check header and environment
    expect(await screen.findByText(dataset.me.email)).toBeInTheDocument();
    expect(screen.getAllByText(dataset.perceive.env_id).length).toBeGreaterThan(0);

    // Check left rail metrics
    expect(screen.getByText(/Cluster Estate/i)).toBeInTheDocument();
    expect(screen.getByText(/Autonomous Swarm/i)).toBeInTheDocument();
    expect(screen.getByText(/Reasoning Quota Budget/i)).toBeInTheDocument();
    expect(screen.getByText(/Topology Tiers/i)).toBeInTheDocument();
  });

  it('gracefully falls back to 2D reduced-motion/headless tier matrix when WebGL is unavailable', async () => {
    resetMockCockpitState(42);
    window.location.hash = '#globe';
    render(<App />);

    // 2D graph matrix is automatically active in JSDOM due to WebGL fallback
    const graph2D = await screen.findByTestId('globe-2d-graph');
    expect(graph2D).toBeInTheDocument();
    expect(screen.getByText(/2D Architectural Tier Matrix/i)).toBeInTheDocument();

    // Verify architectural tiers with services are listed
    expect(screen.getByText(/compute Tier/i)).toBeInTheDocument();
    expect(screen.getByText(/data Tier/i)).toBeInTheDocument();

    // The toggle button shows the option to switch back to 3D Globe
    expect(screen.getByRole('button', { name: /Switch to 3D Globe/i })).toBeInTheDocument();
  });

  it('selecting a node opens Business Context drawer with domain, KPIs, invariants, and evidence', async () => {
    const dataset = resetMockCockpitState(42);
    window.location.hash = '#globe';
    const user = userEvent.setup();
    render(<App />);

    // Wait for 2D matrix to load
    await screen.findByTestId('globe-2d-graph');

    // Select the first discovered service
    const firstService = dataset.perceive.discovered_services[0];
    const nodeCards = await screen.findAllByText(firstService.name);
    await user.click(nodeCards[0]);

    // Business context drawer appears
    const drawer = await screen.findByTestId('business-context-drawer');
    expect(drawer).toBeInTheDocument();

    // Verify service name
    expect(screen.getAllByText(firstService.name).length).toBeGreaterThan(0);

    // Golden signals KPI matrix exists
    expect(within(drawer).getByText(/Golden Signals \(Cloud Monitoring\)/i)).toBeInTheDocument();
    expect(within(drawer).getByText(/^Throughput$/i)).toBeInTheDocument();
    expect(within(drawer).getByText(/^p95 Latency$/i)).toBeInTheDocument();
    expect(within(drawer).getByText(/^Error Ratio$/i)).toBeInTheDocument();

    // Security Posture & Invariants
    expect(within(drawer).getByText(/Security Posture & Invariants/i)).toBeInTheDocument();
    expect(within(drawer).getByText(/IAP Authentication:/i)).toBeInTheDocument();

    // Close drawer
    const closeBtn = drawer.querySelector('button');
    if (closeBtn) {
      await user.click(closeBtn);
      await waitFor(() => {
        expect(screen.queryByTestId('business-context-drawer')).not.toBeInTheDocument();
      });
    }
  });

  it('filters nodes via the left rail search query', async () => {
    const dataset = resetMockCockpitState(42);
    window.location.hash = '#globe';
    const user = userEvent.setup();
    render(<App />);

    await screen.findByTestId('globe-2d-graph');

    const searchInput = screen.getByPlaceholderText(/Filter nodes or domain.../i);
    const targetService = dataset.perceive.discovered_services[0];
    await user.type(searchInput, targetService.name.slice(0, 4));

    expect(screen.getAllByText(new RegExp(targetService.name, 'i')).length).toBeGreaterThan(0);
  });

  it('strictly respects the zero-purple design policy', () => {
    // Confirm no purple hex codes or classes in GlobeView source
    const fs = require('fs');
    const path = require('path');
    const source = fs.readFileSync(
      path.resolve(__dirname, '../components/GlobeView.tsx'),
      'utf-8'
    );
    expect(source.toLowerCase()).not.toContain('purple');
    expect(source.toLowerCase()).not.toContain('violet');
    expect(source.toLowerCase()).not.toContain('#800080');
    expect(source.toLowerCase()).not.toContain('#9333ea');
    expect(source.toLowerCase()).not.toContain('#a855f7');
  });
});
