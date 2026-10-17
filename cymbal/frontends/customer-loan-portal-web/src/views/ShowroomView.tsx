import React from 'react';
import { AlertTriangle, Search } from 'lucide-react';
import type { Vehicle, VehicleCategory } from '../api/types';
import { Badge } from '../components/Badge';
import { Button } from '../components/Button';
import { calculateFinanceMetrics, formatInr } from '../utils/calculator';

export const CATEGORY_LABELS: Record<VehicleCategory | 'ALL', string> = {
  ALL: 'All Vehicles',
  EV_SUV: 'Electric SUVs',
  PASSENGER_SUV: 'Passenger SUVs',
  TWO_WHEELER: 'Two-Wheelers',
  COMMERCIAL_FLEET: 'Commercial Fleet',
};

export const CATEGORY_SINGULAR_LABELS: Record<VehicleCategory, string> = {
  EV_SUV: 'Electric SUV',
  PASSENGER_SUV: 'Passenger SUV',
  TWO_WHEELER: 'Two-Wheeler',
  COMMERCIAL_FLEET: 'Commercial Fleet',
};

export interface ShowroomViewProps {
  vehicles: Vehicle[];
  filteredVehicles: Vehicle[];
  vehiclesLoading: boolean;
  vehiclesError: string | null;
  selectedVehicle: Vehicle;
  categoryFilter: VehicleCategory | 'ALL';
  searchQuery: string;
  sortOrder: 'price_asc' | 'price_desc';
  monthlyIncomeInr: number;
  existingObligationsInr: number;
  onCategoryChange: (cat: VehicleCategory | 'ALL') => void;
  onSearchChange: (q: string) => void;
  onSortChange: (order: 'price_asc' | 'price_desc') => void;
  onRetry: () => void;
  onSelectVehicle: (
    vehicle: Vehicle,
    targetTab: 'calculator' | 'journey'
  ) => void;
}

export const ShowroomView: React.FC<ShowroomViewProps> = ({
  vehicles,
  filteredVehicles,
  vehiclesLoading,
  vehiclesError,
  selectedVehicle,
  categoryFilter,
  searchQuery,
  sortOrder,
  monthlyIncomeInr,
  existingObligationsInr,
  onCategoryChange,
  onSearchChange,
  onSortChange,
  onRetry,
  onSelectVehicle,
}) => {
  return (
    <div className="space-y-6">
      {/* Showroom Top Control Bar */}
      <div className="bg-white border border-slate-200 rounded p-5 shadow-sm flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-ink-heading">
            Explore Vehicles &amp; Instant Loan Estimates
          </h1>
          <p className="text-xs text-ink-muted mt-0.5">
            Compare electric SUVs, passenger SUVs, two-wheelers, and commercial
            fleet vehicles with transparent down payment and monthly EMI
            estimates.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <div className="relative">
            <Search
              size={15}
              strokeWidth={1.75}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-muted"
            />
            <input
              id="showroom-search-input"
              type="text"
              value={searchQuery}
              onChange={(e) => onSearchChange(e.target.value)}
              placeholder="Search make or model..."
              aria-label="Search vehicles by make or model"
              className="pl-8 pr-3 py-1.5 text-xs bg-white border border-slate-300 rounded text-ink-heading placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue"
            />
          </div>

          <select
            id="showroom-sort-select"
            aria-label="Sort vehicles by price"
            value={sortOrder}
            onChange={(e) =>
              onSortChange(e.target.value as 'price_asc' | 'price_desc')
            }
            className="px-3 py-1.5 text-xs bg-white border border-slate-300 rounded text-ink-heading focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gblue"
          >
            <option value="price_asc">Ex-Showroom Price: Low to High</option>
            <option value="price_desc">Ex-Showroom Price: High to Low</option>
          </select>
        </div>
      </div>

      {/* Category Filter Pills */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div
          role="group"
          aria-label="Vehicle Category Filters"
          className="flex flex-wrap items-center gap-2"
        >
          {(
            [
              'ALL',
              'EV_SUV',
              'PASSENGER_SUV',
              'TWO_WHEELER',
              'COMMERCIAL_FLEET',
            ] as const
          ).map((cat) => (
            <Button
              key={cat}
              id={`filter-category-${cat.toLowerCase()}`}
              variant={categoryFilter === cat ? 'primary' : 'outline'}
              size="md"
              onClick={() => onCategoryChange(cat)}
            >
              {CATEGORY_LABELS[cat]}
            </Button>
          ))}
        </div>

        <div className="text-xs text-ink-muted tabular-nums">
          Showing{' '}
          <span className="font-semibold text-ink-heading">
            {filteredVehicles.length}
          </span>{' '}
          of{' '}
          <span className="font-semibold text-ink-heading">
            {vehicles.length}
          </span>{' '}
          vehicles
        </div>
      </div>

      {/* Loading / Error / Showroom Grid */}
      {vehiclesLoading ? (
        <div
          aria-label="Loading vehicle catalog"
          className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 items-start gap-4"
        >
          {[1, 2, 3, 4, 5, 6, 7, 8].map((n) => (
            <div
              key={n}
              className="bg-white border border-slate-200 rounded p-4 space-y-3"
            >
              <div className="h-36 bg-slate-100 rounded" />
              <div className="h-4 w-2/3 bg-slate-100 rounded" />
              <div className="h-6 w-1/2 bg-slate-100 rounded" />
              <div className="h-8 w-full bg-slate-100 rounded" />
            </div>
          ))}
        </div>
      ) : vehiclesError ? (
        <div
          role="alert"
          className="bg-gred-tint border border-gred-border rounded p-4 flex items-center justify-between gap-4"
        >
          <div className="flex items-center gap-3">
            <AlertTriangle size={18} className="text-gred shrink-0" />
            <div>
              <p className="text-sm font-semibold text-ink-heading">
                Unable to load vehicle showroom
              </p>
              <p className="text-xs text-ink-body">{vehiclesError}</p>
            </div>
          </div>
          <Button variant="outline" size="md" onClick={onRetry}>
            Try Again
          </Button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 items-start gap-4">
          {filteredVehicles.map((vehicle) => {
            const isSelected =
              vehicle.vehicle_id === selectedVehicle.vehicle_id;
            const minDpInr = Math.round(
              (vehicle.ex_showroom_inr * vehicle.min_down_payment_pct) / 100
            );
            const previewMetrics = calculateFinanceMetrics({
              exShowroomInr: vehicle.ex_showroom_inr,
              downPaymentInr: minDpInr,
              tenureMonths: 60,
              interestRatePct: vehicle.indicative_rate_pct || 8.75,
              monthlyIncomeInr,
              existingObligationsInr,
              minDownPaymentPct: vehicle.min_down_payment_pct,
            });

            return (
              <article
                key={vehicle.vehicle_id}
                className={`bg-white rounded border transition-colors ${
                  isSelected
                    ? 'border-gblue ring-1 ring-gblue shadow-sm'
                    : 'border-slate-200 hover:border-slate-300 shadow-sm'
                }`}
              >
                {/* Vehicle Illustration Media Box */}
                <div className="relative h-36 bg-surface border-b border-slate-200 overflow-hidden flex items-center justify-center">
                  <img
                    src={vehicle.cdn_image_url}
                    alt={`${vehicle.make} ${vehicle.model}`}
                    className="w-full h-full object-contain p-2"
                  />
                  <div className="absolute top-2.5 left-2.5">
                    <Badge variant="secondary" dot={false}>
                      {CATEGORY_SINGULAR_LABELS[vehicle.category]}
                    </Badge>
                  </div>
                  <div className="absolute top-2.5 right-2.5">
                    <span className="px-2 py-0.5 rounded bg-white/95 border border-slate-200 text-xs font-mono text-ink-muted">
                      {vehicle.vehicle_id}
                    </span>
                  </div>
                </div>

                {/* Card Body */}
                <div className="p-4 space-y-3">
                  <div>
                    <p className="text-xs font-medium text-ink-muted">
                      {vehicle.make}
                    </p>
                    <h2 className="text-sm font-semibold text-ink-heading">
                      {vehicle.model}
                    </h2>
                  </div>

                  {/* Ex-Showroom & Indicative EMI */}
                  <div className="grid grid-cols-2 items-start gap-2 pt-2 border-t border-slate-200">
                    <div>
                      <p className="text-xs text-ink-muted">
                        Ex-Showroom Price
                      </p>
                      <p className="text-base font-semibold text-ink-heading tabular-nums">
                        {formatInr(vehicle.ex_showroom_inr)}
                      </p>
                    </div>
                    <div className="text-right">
                      <p className="text-xs text-ink-muted">
                        EMI · 60 months
                      </p>
                      <p className="text-sm font-semibold text-gblue tabular-nums">
                        {formatInr(previewMetrics.emiInr)}/mo
                      </p>
                    </div>
                  </div>

                  {/* Key Vehicle & Finance Details */}
                  <div className="bg-surface rounded p-2.5 border border-slate-200 space-y-1 text-xs">
                    <div className="flex justify-between">
                      <span className="text-ink-muted">Min Down Payment</span>
                      <span className="font-semibold text-ink-heading tabular-nums">
                        {vehicle.min_down_payment_pct}% ({formatInr(minDpInr)})
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-ink-muted">Interest Rate</span>
                      <span className="font-semibold text-ink-heading tabular-nums">
                        {vehicle.indicative_rate_pct || 8.75}% p.a.
                      </span>
                    </div>
                    {vehicle.powertrain_spec && (
                      <div className="flex justify-between gap-2">
                        <span className="text-ink-muted shrink-0">
                          Powertrain
                        </span>
                        <span
                          title={vehicle.powertrain_spec}
                          className="font-medium text-ink-body truncate"
                        >
                          {vehicle.powertrain_spec}
                        </span>
                      </div>
                    )}
                  </div>

                  {/* Card Actions */}
                  <div className="grid grid-cols-2 items-start gap-2 pt-1">
                    <Button
                      id={`btn-calc-${vehicle.vehicle_id.toLowerCase()}`}
                      variant="outline"
                      size="sm"
                      onClick={() => onSelectVehicle(vehicle, 'calculator')}
                      className="w-full"
                    >
                      Calculate EMI
                    </Button>
                    <Button
                      id={`btn-apply-${vehicle.vehicle_id.toLowerCase()}`}
                      variant="primary"
                      size="sm"
                      onClick={() => onSelectVehicle(vehicle, 'journey')}
                      className="w-full"
                    >
                      Apply for Loan
                    </Button>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
};
