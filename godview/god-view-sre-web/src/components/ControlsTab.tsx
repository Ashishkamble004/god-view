import {
  AlertOctagon,
  AlertTriangle,
  Moon,
  Pause,
  Play,
  Power,
  ShieldAlert,
  Zap,
} from 'lucide-react';
import React, { useState } from 'react';
import type { ChaosDrill, PerceptionReport } from '../api/types';
import {
  Badge,
  Button,
  Card,
  SectionHeader,
  formatNumber,
  formatShortTime,
} from './ui';

export interface ControlsTabProps {
  perceive: PerceptionReport;
  drills: ChaosDrill[];
  onSetKillSwitch: (enabled: boolean, reason: string) => Promise<void>;
  onPauseTenant: () => Promise<void>;
  onResumeTenant: () => Promise<void>;
  onSetHibernate: (enabled: boolean) => Promise<void>;
  onSetSimulatorState: (enabled: boolean) => Promise<void>;
  onTriggerSimulatorBurst: (count: number) => Promise<void>;
  onControlDrill: (drillId: string, action: 'start' | 'stop') => Promise<void>;
  busyKey: string | null;
}

export const ControlsTab: React.FC<ControlsTabProps> = ({
  perceive,
  drills,
  onSetKillSwitch,
  onPauseTenant,
  onResumeTenant,
  onSetHibernate,
  onSetSimulatorState,
  onTriggerSimulatorBurst,
  onControlDrill,
  busyKey,
}) => {
  const [killSwitchArmed, setKillSwitchArmed] = useState(false);
  const [killSwitchReason, setKillSwitchReason] = useState(
    'Operator emergency traffic & autonomy halt from Cockpit'
  );
  const [armedDrillId, setArmedDrillId] = useState<string | null>(null);

  if (!perceive) {
    return (
      <Card className="p-8 text-center">
        <AlertTriangle className="mx-auto h-8 w-8 text-amber-500" />
        <h3 className="mt-3 text-base font-semibold text-slate-900">
          Control Plane Telemetry Not Measured Yet
        </h3>
        <p className="mt-1 text-xs text-slate-500 max-w-md mx-auto">
          The Sovereign control plane <code>/api/v1/perceive</code> endpoint returned no data or is currently unreachable.
        </p>
      </Card>
    );
  }

  const op = perceive.operator_state;
  const safeDrills = drills || [];
  const businessDrills = safeDrills.filter((d) => d.fault_class === 'BUSINESS');
  const infraDrills = safeDrills.filter((d) => d.fault_class === 'INFRA');

  const renderDrillAction = (drill: ChaosDrill) => {
    const isBusy = busyKey === `drill:${drill.drill_id}`;
    if (drill.is_active) {
      return (
        <Button
          variant="secondary"
          size="sm"
          onClick={() => onControlDrill(drill.drill_id, 'stop')}
          disabled={isBusy}
          className="whitespace-nowrap"
        >
          Stop Drill
        </Button>
      );
    }

    if (armedDrillId === drill.drill_id) {
      return (
        <div className="flex items-center gap-1.5 whitespace-nowrap">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setArmedDrillId(null)}
            disabled={isBusy}
          >
            Cancel
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={async () => {
              await onControlDrill(drill.drill_id, 'start');
              setArmedDrillId(null);
            }}
            disabled={isBusy}
          >
            Confirm Start
          </Button>
        </div>
      );
    }

    return (
      <Button
        variant="secondary"
        size="sm"
        onClick={() => setArmedDrillId(drill.drill_id)}
        disabled={isBusy}
        className="whitespace-nowrap"
      >
        Start Drill
      </Button>
    );
  };

  return (
    <div className="space-y-6">
      {/* Scope Safety Banner */}
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-slate-200 bg-slate-50 px-4 py-2.5 text-xs">
        <div className="flex items-center gap-2">
          <Badge variant="info">Proving-ground controls</Badge>
          <span className="text-slate-700">
            All chaos and duty-cycle mutations are strictly hardware-scoped to
            resources carrying label{' '}
            <strong className="font-mono text-slate-900">
              {perceive.managed_scope_label}
            </strong>{' '}
            in <strong className="font-mono">{perceive.env_id}</strong>.
          </span>
        </div>
        <span className="font-mono text-slate-500">
          IAM-only via Sovereign (/api/v1/*)
        </span>
      </div>

      {/* Deliberate Two-Step Red Traffic Kill Switch */}
      <div className="rounded-md border-2 border-red-600 bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <AlertOctagon className="h-5 w-5 text-red-600" />
              <h2 className="text-base font-semibold text-slate-900">
                Traffic &amp; Autonomy Kill Switch (Two-Step Deliberate Control)
              </h2>
              {op.kill_switch_enabled ? (
                <Badge variant="danger" dot>
                  Kill switch engaged (503 TENANT_PAUSED)
                </Badge>
              ) : (
                <Badge variant="outline">Disengaged · Normal operation</Badge>
              )}
            </div>
            <p className="max-w-3xl text-xs text-slate-700">
              Engaging the Kill Switch immediately sets the simulator to{' '}
              <code className="font-mono">enabled=false</code>, instructs the
              orchestrator to reject new requests with{' '}
              <code className="font-mono">503 TENANT_PAUSED</code>, clears all
              active chaos drills, expires pending L2 approvals, lets the single
              in-flight step finish (blast radius 1), and emits{' '}
              <code className="font-mono">godview.operator.kill_switch</code> as
              a planned operator action so the traffic-drop sentinel raises no
              false incident.
            </p>
            {op.kill_switch_enabled && (
              <p className="mt-1 font-mono text-xs font-semibold text-red-700">
                Active Reason: {op.kill_switch_reason} · Actor:{' '}
                {op.kill_switch_actor} · At:{' '}
                {formatShortTime(op.kill_switch_updated_at)}
              </p>
            )}
          </div>

          <div>
            {op.kill_switch_enabled ? (
              <Button
                variant="secondary"
                size="md"
                onClick={async () => {
                  await onSetKillSwitch(false, 'Operator restored normal traffic');
                  setKillSwitchArmed(false);
                }}
                disabled={busyKey === 'kill-switch'}
              >
                Disengage Kill Switch &amp; Restore Traffic
              </Button>
            ) : !killSwitchArmed ? (
              <button
                type="button"
                onClick={() => setKillSwitchArmed(true)}
                className="inline-flex items-center gap-1.5 rounded border border-red-600 bg-red-50 px-4 py-2 text-xs font-semibold text-red-800 hover:bg-red-100 focus:outline-none focus:ring-2 focus:ring-red-600 focus:ring-offset-1"
              >
                <ShieldAlert className="h-4 w-4 text-red-700" />
                Step 1: Arm Traffic Kill Switch
              </button>
            ) : null}
          </div>
        </div>

        {/* Step 2 Confirmation Panel (Only visible when armed) */}
        {killSwitchArmed && !op.kill_switch_enabled && (
          <div className="mt-4 rounded border border-red-300 bg-red-50 p-4">
            <div className="flex flex-wrap items-end justify-between gap-4">
              <div className="flex-1 min-w-[260px]">
                <label
                  htmlFor="kill-switch-reason-input"
                  className="block text-xs font-semibold text-red-900"
                >
                  Step 2: Confirm Operator Reason for Audit Log
                </label>
                <input
                  id="kill-switch-reason-input"
                  type="text"
                  value={killSwitchReason}
                  onChange={(e) => setKillSwitchReason(e.target.value)}
                  className="mt-1 w-full rounded border border-red-300 bg-white px-3 py-1.5 text-xs text-slate-900 focus:border-red-600 focus:outline-none focus:ring-2 focus:ring-red-600"
                />
              </div>
              <div className="flex items-center gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => setKillSwitchArmed(false)}
                >
                  Cancel
                </Button>
                <Button
                  variant="danger"
                  size="sm"
                  onClick={async () => {
                    await onSetKillSwitch(true, killSwitchReason);
                    setKillSwitchArmed(false);
                  }}
                  disabled={busyKey === 'kill-switch'}
                >
                  Confirm &amp; Engage Kill Switch
                </Button>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Duty Cycle: Pause/Resume, Hibernate/Wake & Synthetic Simulator */}
      <div className="grid grid-cols-1 items-stretch gap-4 lg:grid-cols-3">
        <Card className="flex h-full flex-col justify-between p-4">
          <div>
            <SectionHeader
              title="Scheduled Pause / Resume"
              subtitle={`Window: ${op.pause_schedule_ist}`}
              right={
                <Badge variant={op.tenant_paused ? 'warning' : 'success'} dot>
                  {op.tenant_paused ? 'Paused' : 'Active'}
                </Badge>
              }
            />
            <p className="mt-2.5 text-xs text-slate-600">
              Cools all warm court agents to <code className="font-mono">min_instances=0</code> and halts the synthetic simulator while 0-token sentinels continue watching.
            </p>
          </div>
          <div className="mt-4 flex items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={onPauseTenant}
              disabled={op.tenant_paused || busyKey === 'pause'}
            >
              <Pause className="h-3.5 w-3.5" />
              Pause Tenant
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={onResumeTenant}
              disabled={!op.tenant_paused || busyKey === 'resume'}
            >
              <Play className="h-3.5 w-3.5" />
              Resume Tenant
            </Button>
          </div>
        </Card>

        <Card className="flex h-full flex-col justify-between p-4">
          <div>
            <SectionHeader
              title="Environment Hibernate / Wake"
              subtitle={`Cloud Run Job: ${perceive.hibernate_job_name}`}
              right={
                <Badge variant={op.hibernated ? 'warning' : 'secondary'} dot>
                  {op.hibernated ? 'Hibernated (60s tick)' : 'Awake (10s tick)'}
                </Badge>
              }
            />
            <p className="mt-2.5 text-xs text-slate-600">
              Scales GKE deployments to 0, stops batch worker VM, sets Cloud SQL <code className="font-mono">activation_policy=NEVER</code>, and throttles sentinels to 60s. Wake takes ~5–8 min.
            </p>
          </div>
          <div className="mt-4 flex items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => onSetHibernate(true)}
              disabled={op.hibernated || busyKey === 'hibernate'}
            >
              <Moon className="h-3.5 w-3.5" />
              Hibernate
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={() => onSetHibernate(false)}
              disabled={!op.hibernated || busyKey === 'hibernate'}
            >
              <Power className="h-3.5 w-3.5" />
              Wake Environment
            </Button>
          </div>
        </Card>

        <Card className="flex h-full flex-col justify-between p-4">
          <div>
            <SectionHeader
              title="Synthetic Customer Stream"
              subtitle={`${op.simulator_window_ist} · ${op.simulator_specimen_ratio}`}
              right={
                <Badge variant={op.simulator_enabled ? 'success' : 'muted'} dot>
                  {op.simulator_enabled ? 'Streaming' : 'Stopped'}
                </Badge>
              }
            />
            <p className="mt-2.5 text-xs text-slate-700 num-tabular">
              Journeys today:{' '}
              <strong>
                {formatNumber(op.simulator_journeys_today)} /{' '}
                {formatNumber(op.simulator_target_per_day)}
              </strong>{' '}
              · Marker: <code className="font-mono">X-Synthetic: true</code>
            </p>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => onSetSimulatorState(!op.simulator_enabled)}
              disabled={busyKey === 'simulator-state'}
            >
              {op.simulator_enabled ? 'Pause Simulator' : 'Enable Simulator'}
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={() => onTriggerSimulatorBurst(5)}
              disabled={busyKey === 'simulator-burst'}
            >
              <Zap className="h-3.5 w-3.5" />
              +5 Journey Burst
            </Button>
          </div>
        </Card>
      </div>

      {/* Opt-In Chaos Drills (5 Business-Logic + 3 Infra) */}
      <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-12">
        {!drills ? (
          <Card className="p-8 text-center xl:col-span-12">
            <AlertTriangle className="mx-auto h-8 w-8 text-amber-500" />
            <h3 className="mt-3 text-base font-semibold text-slate-900">
              Chaos Drills Telemetry Not Measured Yet
            </h3>
            <p className="mt-1 text-xs text-slate-500 max-w-md mx-auto">
              The Sovereign control plane <code>/api/v1/chaos/drills</code> endpoint returned no data or is currently unreachable.
            </p>
          </Card>
        ) : (
          <>
            <Card className="p-4 xl:col-span-7">
              <SectionHeader
                title="Business-Logic Chaos Drills (5 Drills — Infra Golden Signals Stay Healthy)"
                subtitle="Opt-in proving-ground drills proving God-View catches silent business corruption while HTTP 200 and P95 stay green"
              />
              <div className="mt-3 space-y-3">
                {businessDrills.map((drill) => (
                  <div
                    key={drill.drill_id}
                    className="rounded border border-slate-200 bg-slate-50 p-3.5 text-xs"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div>
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-semibold text-slate-900">
                            {drill.title}
                          </span>
                          <Badge variant="success">Infra signals: healthy</Badge>
                          <Badge variant="outline" className="num-tabular">
                            Target {drill.mttd_target}
                          </Badge>
                          {drill.is_active && (
                            <Badge variant="warning" dot>
                              Active drill
                            </Badge>
                          )}
                        </div>
                        <p className="mt-1 font-mono text-slate-600">
                          id: {drill.drill_id} · target: {drill.target_service}
                        </p>
                        <p className="mt-1 text-slate-700">
                          Expected Detection: {drill.expected_detection}
                        </p>
                      </div>

                      <div>{renderDrillAction(drill)}</div>
                    </div>
                  </div>
                ))}
              </div>
            </Card>

            <Card className="p-4 xl:col-span-5">
              <SectionHeader
                title="Infrastructure & Fate-Isolation Drills (3 Drills)"
                subtitle="Validates L3 autonomous remediation and Sovereign fate isolation"
              />
              <div className="mt-3 space-y-3">
                {infraDrills.map((drill) => (
                  <div
                    key={drill.drill_id}
                    className="rounded border border-slate-200 bg-slate-50 p-3.5 text-xs"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div>
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-semibold text-slate-900">
                            {drill.title}
                          </span>
                          <Badge
                            variant={
                              drill.infra_golden_signals === 'red'
                                ? 'danger'
                                : 'success'
                            }
                          >
                            {drill.infra_golden_signals === 'red'
                              ? 'Infra signals: degraded'
                              : 'Infra signals: healthy'}
                          </Badge>
                          {drill.is_active && (
                            <Badge variant="warning" dot>
                              Active drill
                            </Badge>
                          )}
                        </div>
                        <p className="mt-1 font-mono text-slate-600">
                          id: {drill.drill_id} · target: {drill.target_service}
                        </p>
                        <p className="mt-1 text-slate-700">
                          {drill.expected_detection} ({drill.mttd_target})
                        </p>
                      </div>

                      <div>{renderDrillAction(drill)}</div>
                    </div>
                  </div>
                ))}
              </div>
            </Card>
          </>
        )}
      </div>
    </div>
  );
};
