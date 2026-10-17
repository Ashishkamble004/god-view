import React from 'react';
import { Calculator, Lock } from 'lucide-react';
import type { Vehicle } from '../api/types';
import { Badge } from '../components/Badge';
import { Button } from '../components/Button';
import { formatInr, type FinanceMetrics } from '../utils/calculator';

export interface CalculatorViewProps {
  vehicles: Vehicle[];
  selectedVehicle: Vehicle;
  downPaymentInr: number;
  tenureMonths: number;
  interestRatePct: number;
  monthlyIncomeInr: number;
  existingObligationsInr: number;
  financeMetrics: FinanceMetrics;
  onSelectVehicle: (vehicle: Vehicle) => void;
  onDownPaymentChange: (dp: number) => void;
  onTenureChange: (months: number) => void;
  onMonthlyIncomeChange: (income: number) => void;
  onExistingObligationsChange: (obligations: number) => void;
  onProceedToJourney: () => void;
}

export const CalculatorView: React.FC<CalculatorViewProps> = ({
  vehicles,
  selectedVehicle,
  downPaymentInr,
  tenureMonths,
  interestRatePct,
  monthlyIncomeInr,
  existingObligationsInr,
  financeMetrics,
  onSelectVehicle,
  onDownPaymentChange,
  onTenureChange,
  onMonthlyIncomeChange,
  onExistingObligationsChange,
  onProceedToJourney,
}) => {
  const minDpInr = Math.round(
    (selectedVehicle.ex_showroom_inr * selectedVehicle.min_down_payment_pct) /
      100
  );
  const maxDpInr = Math.round(selectedVehicle.ex_showroom_inr * 0.8);

  return (
    <div className="space-y-6">
      <div className="bg-white border border-slate-200 rounded p-5 shadow-sm flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-ink-heading">
            EMI &amp; Loan Eligibility Calculator
          </h1>
          <p className="text-xs text-ink-muted mt-0.5">
            Adjust your down payment, repayment tenure, and monthly income to
            preview your monthly EMI and loan eligibility.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={financeMetrics.foirEligible ? 'success' : 'danger'}>
            Income Share {financeMetrics.foirPct}% (Max 60%)
          </Badge>
          <Badge variant={financeMetrics.ltvEligible ? 'success' : 'warning'}>
            Loan-to-Value {financeMetrics.ltvPct}% (Max 90%)
          </Badge>
          <Button
            id="calc-proceed-to-journey-btn"
            variant="primary"
            size="md"
            onClick={onProceedToJourney}
          >
            Continue to Loan Application
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 items-start gap-6">
        {/* Left 7 Cols: Interactive Sliders & Inputs */}
        <div className="lg:col-span-7 bg-white border border-slate-200 rounded p-5 shadow-sm space-y-5">
          <div>
            <label
              htmlFor="calc-vehicle-select"
              className="block text-xs font-semibold text-ink-heading mb-1.5"
            >
              Selected Vehicle
            </label>
            <select
              id="calc-vehicle-select"
              value={selectedVehicle.vehicle_id}
              onChange={(e) => {
                const found = vehicles.find(
                  (v) => v.vehicle_id === e.target.value
                );
                if (found) onSelectVehicle(found);
              }}
              className="w-full px-3 py-2 text-xs bg-white border border-slate-300 rounded text-ink-heading focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue"
            >
              {vehicles.map((v) => (
                <option key={v.vehicle_id} value={v.vehicle_id}>
                  {v.make} {v.model} — Ex-Showroom {formatInr(v.ex_showroom_inr)}
                </option>
              ))}
            </select>
          </div>

          {/* Down Payment Slider */}
          <div className="space-y-1.5">
            <div className="flex justify-between text-xs">
              <label
                htmlFor="calc-downpayment-range"
                className="font-semibold text-ink-heading"
              >
                Down Payment (Minimum {selectedVehicle.min_down_payment_pct}%)
              </label>
              <span className="font-semibold text-gblue tabular-nums">
                {formatInr(downPaymentInr)} ({financeMetrics.downPaymentPct}%)
              </span>
            </div>
            <input
              id="calc-downpayment-range"
              type="range"
              min={minDpInr}
              max={maxDpInr}
              step={5000}
              value={downPaymentInr}
              aria-valuetext={`${formatInr(downPaymentInr)} (${financeMetrics.downPaymentPct}% down payment)`}
              onChange={(e) => onDownPaymentChange(Number(e.target.value))}
              className="w-full accent-gblue focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue rounded"
            />
            <div className="flex justify-between text-xs text-ink-muted tabular-nums">
              <span>
                Min: {formatInr(minDpInr)} ({selectedVehicle.min_down_payment_pct}%)
              </span>
              <span>Max: {formatInr(maxDpInr)} (80%)</span>
            </div>
          </div>

          {/* Repayment Tenure Slider */}
          <div className="space-y-1.5">
            <div className="flex justify-between text-xs">
              <label
                htmlFor="calc-tenure-range"
                className="font-semibold text-ink-heading"
              >
                Repayment Tenure
              </label>
              <span className="font-semibold text-ink-heading tabular-nums">
                {tenureMonths} Months ({(tenureMonths / 12).toFixed(1)} Years)
              </span>
            </div>
            <input
              id="calc-tenure-range"
              type="range"
              min={12}
              max={84}
              step={6}
              value={tenureMonths}
              aria-valuetext={`${tenureMonths} months (${(tenureMonths / 12).toFixed(1)} years)`}
              onChange={(e) => onTenureChange(Number(e.target.value))}
              className="w-full accent-gblue focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue rounded"
            />
            <div className="flex justify-between text-xs text-ink-muted tabular-nums">
              <span>Min: 12 months (1 yr)</span>
              <span>Max: 84 months (7 yrs)</span>
            </div>
          </div>

          {/* Read-only Indicative Interest Rate (determined by vehicle category) */}
          <div className="p-3 rounded bg-surface border border-slate-200 flex items-center justify-between gap-3 text-xs">
            <div className="flex items-center gap-2">
              <Lock size={14} className="text-ink-muted shrink-0" />
              <div>
                <span className="font-semibold text-ink-heading block">
                  Annual Interest Rate (Standard Vehicle Tier)
                </span>
                <span className="text-ink-muted">
                  Reducing-balance rate set by vehicle category for{' '}
                  {selectedVehicle.make} {selectedVehicle.model}
                </span>
              </div>
            </div>
            <span
              data-testid="calculator-readonly-rate"
              className="font-semibold text-ink-heading tabular-nums whitespace-nowrap px-2.5 py-1 rounded bg-white border border-slate-200"
            >
              Indicative {interestRatePct.toFixed(2)}% p.a.
            </span>
          </div>

          {/* Monthly Income & Existing Obligations */}
          <div className="grid grid-cols-1 sm:grid-cols-2 items-start gap-4 pt-2 border-t border-slate-200">
            <div>
              <div className="flex items-center justify-between text-xs mb-1">
                <label
                  htmlFor="calc-income-input"
                  className="font-semibold text-ink-heading"
                >
                  Net Monthly Income
                </label>
                <span className="font-semibold text-gblue tabular-nums">
                  {formatInr(monthlyIncomeInr)}
                </span>
              </div>
              <input
                id="calc-income-input"
                type="number"
                min={15000}
                step={5000}
                value={monthlyIncomeInr}
                onChange={(e) =>
                  onMonthlyIncomeChange(Math.max(1, Number(e.target.value)))
                }
                className="w-full px-3 py-1.5 text-xs bg-white border border-slate-300 rounded text-ink-heading tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue"
              />
              <p className="text-xs text-ink-muted mt-1 tabular-nums">
                Formatted: {formatInr(monthlyIncomeInr)} / month
              </p>
            </div>
            <div>
              <div className="flex items-center justify-between text-xs mb-1">
                <label
                  htmlFor="calc-obligations-input"
                  className="font-semibold text-ink-heading"
                >
                  Existing Monthly EMIs
                </label>
                <span className="font-semibold text-ink-heading tabular-nums">
                  {formatInr(existingObligationsInr)}
                </span>
              </div>
              <input
                id="calc-obligations-input"
                type="number"
                min={0}
                step={1000}
                value={existingObligationsInr}
                onChange={(e) =>
                  onExistingObligationsChange(
                    Math.max(0, Number(e.target.value))
                  )
                }
                className="w-full px-3 py-1.5 text-xs bg-white border border-slate-300 rounded text-ink-heading tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue"
              />
              <p className="text-xs text-ink-muted mt-1 tabular-nums">
                Formatted: {formatInr(existingObligationsInr)} / month
              </p>
            </div>
          </div>
        </div>

        {/* Right 5 Cols: Financial Summary & Eligibility Bar */}
        <div className="lg:col-span-5 bg-white border border-slate-200 rounded p-5 shadow-sm space-y-4">
          <div className="flex items-center justify-between border-b border-slate-200 pb-3">
            <div>
              <p className="text-xs text-ink-muted">
                Estimated Monthly Instalment (EMI)
              </p>
              <p
                data-testid="calculator-emi-output"
                className="text-2xl font-semibold text-ink-heading tabular-nums tracking-tight"
              >
                {formatInr(financeMetrics.emiInr)}
                <span className="text-xs font-normal text-ink-muted">
                  {' '}
                  / month
                </span>
              </p>
            </div>
            <Calculator size={22} className="text-gblue" />
          </div>

          <div className="grid grid-cols-2 items-start gap-3 text-xs">
            <div className="p-3 rounded bg-surface border border-slate-200">
              <p className="text-ink-muted">Loan Amount</p>
              <p className="text-sm font-semibold text-ink-heading tabular-nums mt-0.5">
                {formatInr(financeMetrics.principalInr)}
              </p>
            </div>
            <div className="p-3 rounded bg-surface border border-slate-200">
              <p className="text-ink-muted">Total Interest Payable</p>
              <p className="text-sm font-semibold text-ink-heading tabular-nums mt-0.5">
                {formatInr(financeMetrics.totalInterestInr)}
              </p>
            </div>
            <div className="p-3 rounded bg-surface border border-slate-200">
              <p className="text-ink-muted">Total Payable (Principal + Interest)</p>
              <p className="text-sm font-semibold text-ink-heading tabular-nums mt-0.5">
                {formatInr(financeMetrics.totalPayableInr)}
              </p>
            </div>
            <div className="p-3 rounded bg-surface border border-slate-200">
              <p className="text-ink-muted">Loan-to-Value (LTV)</p>
              <p className="text-sm font-semibold text-ink-heading tabular-nums mt-0.5">
                {financeMetrics.ltvPct}%
              </p>
            </div>
          </div>

          {/* Monthly Income Eligibility (FOIR) Bar */}
          <div className="p-3.5 rounded bg-surface border border-slate-200 space-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="font-semibold text-ink-heading">
                Fixed Obligation to Income Ratio (FOIR)
              </span>
              <span
                className={`font-semibold tabular-nums ${
                  financeMetrics.foirEligible ? 'text-ggreen' : 'text-gred'
                }`}
              >
                {financeMetrics.foirPct}% / 60% Max
              </span>
            </div>
            <div className="w-full h-2 bg-slate-200 dark:bg-slate-800 rounded overflow-hidden">
              <div
                className={`h-full ${
                  financeMetrics.foirEligible ? 'bg-ggreen' : 'bg-gred'
                }`}
                style={{
                  width: `${Math.min(100, financeMetrics.foirPct)}%`,
                }}
              />
            </div>
            <p className="text-xs text-ink-muted">
              Your total EMIs would use{' '}
              <span className="font-semibold text-ink-heading tabular-nums">
                {financeMetrics.foirPct}%
              </span>{' '}
              of your monthly income — we allow up to 60%.{' '}
              {financeMetrics.foirEligible
                ? 'You meet our income eligibility requirement.'
                : 'Try increasing your down payment or choosing a longer repayment tenure.'}
            </p>
          </div>

          {/* Annual Repayment Breakdown Table */}
          <div className="border border-slate-200 rounded overflow-hidden">
            <div className="bg-surface px-3 py-2 border-b border-slate-200 text-xs font-semibold text-ink-heading">
              Annual Repayment Schedule
            </div>
            <div className="max-h-48 overflow-y-auto">
              <table className="w-full text-left text-xs">
                <thead className="sticky top-0 bg-surface border-b border-slate-200 text-ink-muted">
                  <tr>
                    <th className="py-1.5 px-3 font-semibold">Year</th>
                    <th className="py-1.5 px-3 font-semibold text-right">
                      Principal
                    </th>
                    <th className="py-1.5 px-3 font-semibold text-right">
                      Interest
                    </th>
                    <th className="py-1.5 px-3 font-semibold text-right">
                      Closing Balance
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-200 tabular-nums">
                  {financeMetrics.schedule.map((row) => (
                    <tr key={row.year} className="hover:bg-surface">
                      <td className="py-1.5 px-3 font-medium text-ink-heading">
                        Year {row.year}
                      </td>
                      <td className="py-1.5 px-3 text-right text-ink-body">
                        {formatInr(row.principalPaidInr)}
                      </td>
                      <td className="py-1.5 px-3 text-right text-ink-body">
                        {formatInr(row.interestPaidInr)}
                      </td>
                      <td className="py-1.5 px-3 text-right font-semibold text-ink-heading">
                        {formatInr(row.closingBalanceInr)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
