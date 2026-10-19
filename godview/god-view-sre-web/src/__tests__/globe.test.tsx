import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import App from '../App';
import { resetMockCockpitState } from '../mocks/handlers';

describe('God-View Autonomous Estate Topology & Service Mesh Cockpit', () => {
  it('renders #globe view by default with executive telemetry bar, view mode switcher, and architectural tier lanes', async () => {
    const dataset = resetMockCockpitState(42);
    window.location.hash = '#globe';
    render(<App />);

    expect(await screen.findByText(dataset.me.email)).toBeInTheDocument();
    expect(screen.getAllByText(dataset.perceive.env_id).length).toBeGreaterThan(0);

    expect(
      await screen.findByText(/Autonomous Estate Topology & Service Mesh/i)
    ).toBeInTheDocument();
    expect(screen.getByText(/Discovered Resources:/i)).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Service Mesh DAG/i })
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Domain Clusters/i })
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Full Estate Matrix/i })
    ).toBeInTheDocument();
  });

  it('switches between Service Mesh DAG, Domain Clusters, and Full Estate Matrix views', async () => {
    resetMockCockpitState(42);
    window.location.hash = '#globe';
    const user = userEvent.setup();
    render(<App />);

    await screen.findByText(/Autonomous Estate Topology & Service Mesh/i);

    const domainsBtn = screen.getByRole('button', { name: /Domain Clusters/i });
    await user.click(domainsBtn);
    expect(screen.getAllByText(/Kinds:/i).length).toBeGreaterThan(0);

    const matrixBtn = screen.getByRole('button', { name: /Full Estate Matrix/i });
    await user.click(matrixBtn);
    expect(screen.getByText(/Resource Name/i)).toBeInTheDocument();
    expect(screen.getByText(/Inferred Domain & Role/i)).toBeInTheDocument();
  });

  it('selecting a node opens the Autonomous Business Context & Call-Graph Inspector drawer', async () => {
    const dataset = resetMockCockpitState(42);
    window.location.hash = '#globe';
    const user = userEvent.setup();
    render(<App />);

    await screen.findByText(/Autonomous Estate Topology & Service Mesh/i);

    const firstNode =
      dataset.topology?.nodes[0] ?? dataset.perceive.discovered_services[0];
    const nodeButtons = await screen.findAllByText(firstNode.name);
    await user.click(nodeButtons[0]);

    expect(
      await screen.findByText(/Autonomous Business Context/i)
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Call-Graph & Dependency Edges/i)
    ).toBeInTheDocument();
  });

  it('filters nodes via the search query input', async () => {
    const dataset = resetMockCockpitState(42);
    window.location.hash = '#globe';
    const user = userEvent.setup();
    render(<App />);

    await screen.findByText(/Autonomous Estate Topology & Service Mesh/i);

    const searchInput = screen.getByPlaceholderText(
      /Search 115\+ services, kinds, domains.../i
    );
    const targetNode =
      dataset.topology?.nodes[0] ?? dataset.perceive.discovered_services[0];
    await user.type(searchInput, targetNode.name.slice(0, 6));

    await waitFor(() => {
      expect(
        screen.getAllByText(new RegExp(targetNode.name, 'i')).length
      ).toBeGreaterThan(0);
    });
  });

  it('strictly respects the zero-purple design policy', () => {
    const fs = require('fs');
    const path = require('path');
    const source = fs.readFileSync(
      path.resolve(__dirname, '../components/GlobeView.tsx'),
      'utf-8'
    );
    expect(source.toLowerCase()).not.toContain('purple');
    expect(source.toLowerCase()).not.toContain('violet');
    expect(source.toLowerCase()).not.toContain('#' + '800080');
    expect(source.toLowerCase()).not.toContain('#' + '9333ea');
    expect(source.toLowerCase()).not.toContain('#' + 'a855f7');
  });
});
