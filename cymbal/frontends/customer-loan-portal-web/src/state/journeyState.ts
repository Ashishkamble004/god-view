import type {
  LoanStatus,
  TelemetryStreamEvent,
  UtilityType,
} from '../api/types';

export type WizardStepStatus =
  | 'PENDING'
  | 'IN_PROGRESS'
  | 'COMPLETED'
  | 'SKIPPED'
  | 'BLOCKED'
  | 'REJECTED';

export interface WizardStepItem {
  stepNumber: number;
  code: 'KYC' | 'LOS' | 'FLEET' | 'INS' | 'CBS';
  title: string;
  shortLabel: string;
  status: WizardStepStatus;
  detail: string;
}

export interface JourneyWizardState {
  utilityType: UtilityType;
  activeStepIndex: number;
  loanId: string | null;
  journeyId: string | null;
  overallStatus: LoanStatus | 'DRAFT';
  steps: WizardStepItem[];
}

export function stepStatusLabel(status: WizardStepStatus): string {
  switch (status) {
    case 'COMPLETED':
      return 'Completed';
    case 'IN_PROGRESS':
      return 'In progress';
    case 'SKIPPED':
      return 'Optional (Skipped)';
    case 'BLOCKED':
      return 'Action needed';
    case 'REJECTED':
      return 'Not eligible';
    default:
      return 'Not started';
  }
}

export function buildWizardSteps(utilityType: UtilityType): WizardStepItem[] {
  const isFleet = utilityType === 'COMMERCIAL_FLEET_TOPUP';
  return [
    {
      stepNumber: 1,
      code: 'KYC',
      title: 'Identity Check',
      shortLabel: 'Identity Check',
      status: 'PENDING',
      detail: 'Verify your PAN & Aadhaar details',
    },
    {
      stepNumber: 2,
      code: 'LOS',
      title: 'Loan Approval',
      shortLabel: 'Loan Approval',
      status: 'PENDING',
      detail: 'Instant income & repayment eligibility check',
    },
    {
      stepNumber: 3,
      code: 'FLEET',
      title: 'Fleet Top-Up (Optional)',
      shortLabel: 'Fleet Top-Up',
      status: isFleet ? 'PENDING' : 'SKIPPED',
      detail: isFleet
        ? 'Working-capital credit line for commercial operators'
        : 'Optional for personal vehicle loans',
    },
    {
      stepNumber: 4,
      code: 'INS',
      title: 'Insurance & FASTag',
      shortLabel: 'Insurance & FASTag',
      status: 'PENDING',
      detail: 'Comprehensive motor cover & FASTag wallet linkage',
    },
    {
      stepNumber: 5,
      code: 'CBS',
      title: 'Bank Payout',
      shortLabel: 'Bank Payout',
      status: 'PENDING',
      detail: 'Sanction letter, e-Mandate & dealer payout',
    },
  ];
}

export function initialWizardState(
  utilityType: UtilityType = 'RETAIL_AUTO_LOAN'
): JourneyWizardState {
  return {
    utilityType,
    activeStepIndex: 0,
    loanId: null,
    journeyId: null,
    overallStatus: 'DRAFT',
    steps: buildWizardSteps(utilityType),
  };
}

export type JourneyWizardAction =
  | { type: 'SET_UTILITY_TYPE'; utilityType: UtilityType }
  | { type: 'SET_ACTIVE_STEP'; index: number }
  | {
      type: 'APPLICATION_SUBMITTED';
      loanId: string;
      journeyId: string;
      utilityType: UtilityType;
      finalStatus: LoanStatus;
    }
  | { type: 'SSE_EVENT'; event: TelemetryStreamEvent }
  | { type: 'RESET' };

export function applyLoanStatusToSteps(
  steps: WizardStepItem[],
  utilityType: UtilityType,
  status: LoanStatus
): WizardStepItem[] {
  const isFleet = utilityType === 'COMMERCIAL_FLEET_TOPUP';
  return steps.map((step) => {
    if (step.code === 'FLEET' && !isFleet) {
      return { ...step, status: 'SKIPPED' };
    }
    if (status === 'MODEL_ARMOR_BLOCKED') {
      if (step.code === 'KYC') return { ...step, status: 'BLOCKED' };
      return {
        ...step,
        status: step.code === 'FLEET' && !isFleet ? 'SKIPPED' : 'PENDING',
      };
    }
    if (status === 'REJECTED') {
      if (step.code === 'KYC') return { ...step, status: 'COMPLETED' };
      if (step.code === 'LOS') return { ...step, status: 'REJECTED' };
      return {
        ...step,
        status: step.code === 'FLEET' && !isFleet ? 'SKIPPED' : 'PENDING',
      };
    }
    if (status === 'KYC_VERIFIED') {
      if (step.code === 'KYC') return { ...step, status: 'COMPLETED' };
      if (step.code === 'LOS') return { ...step, status: 'IN_PROGRESS' };
      return step;
    }
    if (status === 'APPROVED') {
      if (step.code === 'KYC' || step.code === 'LOS') {
        return { ...step, status: 'COMPLETED' };
      }
      if (step.code === 'FLEET') {
        return { ...step, status: isFleet ? 'COMPLETED' : 'SKIPPED' };
      }
      if (step.code === 'INS') return { ...step, status: 'IN_PROGRESS' };
      return step;
    }
    if (status === 'INSURED' || status === 'DISBURSEMENT_PENDING') {
      if (step.code === 'CBS') return { ...step, status: 'IN_PROGRESS' };
      if (step.code === 'FLEET' && !isFleet) {
        return { ...step, status: 'SKIPPED' };
      }
      return { ...step, status: 'COMPLETED' };
    }
    if (status === 'DISBURSED') {
      if (step.code === 'FLEET' && !isFleet) {
        return { ...step, status: 'SKIPPED' };
      }
      return { ...step, status: 'COMPLETED' };
    }
    return step;
  });
}

export function journeyWizardReducer(
  state: JourneyWizardState,
  action: JourneyWizardAction
): JourneyWizardState {
  switch (action.type) {
    case 'SET_UTILITY_TYPE': {
      return {
        ...state,
        utilityType: action.utilityType,
        steps: buildWizardSteps(action.utilityType),
      };
    }
    case 'SET_ACTIVE_STEP': {
      const clamped = Math.max(
        0,
        Math.min(state.steps.length - 1, action.index)
      );
      return {
        ...state,
        activeStepIndex: clamped,
      };
    }
    case 'APPLICATION_SUBMITTED': {
      const baseSteps = buildWizardSteps(action.utilityType);
      const updatedSteps = applyLoanStatusToSteps(
        baseSteps,
        action.utilityType,
        action.finalStatus
      );
      const activeIdx =
        action.finalStatus === 'MODEL_ARMOR_BLOCKED'
          ? 0
          : action.finalStatus === 'REJECTED'
            ? 1
            : 4;
      return {
        ...state,
        utilityType: action.utilityType,
        loanId: action.loanId,
        journeyId: action.journeyId,
        overallStatus: action.finalStatus,
        activeStepIndex: activeIdx,
        steps: updatedSteps,
      };
    }
    case 'SSE_EVENT': {
      const { event } = action;
      if (
        state.journeyId &&
        event.journey_id !== state.journeyId &&
        state.loanId &&
        event.loan_id !== state.loanId
      ) {
        return state;
      }
      const nextStatus = event.status_to || state.overallStatus;
      const updatedSteps =
        nextStatus !== 'DRAFT'
          ? applyLoanStatusToSteps(state.steps, state.utilityType, nextStatus)
          : state.steps;
      return {
        ...state,
        overallStatus: nextStatus,
        steps: updatedSteps,
      };
    }
    case 'RESET':
      return initialWizardState(state.utilityType);
    default:
      return state;
  }
}

export interface TelemetryState {
  connected: boolean;
  reconnectAttempt: number;
  events: TelemetryStreamEvent[];
  seenIds: Record<string, true>;
  byJourneyId: Record<string, TelemetryStreamEvent[]>;
  latestStatusByLoanId: Record<string, LoanStatus>;
}

export const initialTelemetryState: TelemetryState = {
  connected: false,
  reconnectAttempt: 0,
  events: [],
  seenIds: {},
  byJourneyId: {},
  latestStatusByLoanId: {},
};

export type TelemetryAction =
  | { type: 'CONNECTED' }
  | { type: 'DISCONNECTED'; attempt: number }
  | { type: 'EVENT_RECEIVED'; event: TelemetryStreamEvent };

export function telemetryReducer(
  state: TelemetryState,
  action: TelemetryAction
): TelemetryState {
  switch (action.type) {
    case 'CONNECTED':
      return { ...state, connected: true, reconnectAttempt: 0 };
    case 'DISCONNECTED':
      return { ...state, connected: false, reconnectAttempt: action.attempt };
    case 'EVENT_RECEIVED': {
      const { event } = action;
      if (event.event_type === 'heartbeat') {
        return { ...state, connected: true };
      }
      if (state.seenIds[event.event_id]) {
        return state;
      }
      const nextEvents = [event, ...state.events].slice(0, 100);
      const journeyEvents = [
        ...(state.byJourneyId[event.journey_id] || []),
        event,
      ];
      const nextStatusMap = { ...state.latestStatusByLoanId };
      if (event.loan_id && event.status_to) {
        nextStatusMap[event.loan_id] = event.status_to;
      }
      return {
        ...state,
        connected: true,
        seenIds: { ...state.seenIds, [event.event_id]: true },
        events: nextEvents,
        byJourneyId: {
          ...state.byJourneyId,
          [event.journey_id]: journeyEvents,
        },
        latestStatusByLoanId: nextStatusMap,
      };
    }
    default:
      return state;
  }
}
