"""Utility 4: Motor Insurance & FastTag Add-On Service."""

from motor_insurance.main import app, create_app
from motor_insurance.models import (
    ChaosFaultClass,
    ChaosFaultStateRow,
    ChaosFaultType,
    InsuranceIssueRequest,
    InsuranceIssueResponse,
    InsurancePolicyRow,
)
from motor_insurance.repository import (
    InMemoryInsuranceRepository,
    InsuranceRepositoryProtocol,
    PostgresInsuranceRepository,
    RepositoryConfigurationError,
    RepositoryError,
    create_repository_from_env,
)
from motor_insurance.service import LoanNotApprovedForInsuranceError, MotorInsuranceService

__all__ = [
    "ChaosFaultClass",
    "ChaosFaultStateRow",
    "ChaosFaultType",
    "InMemoryInsuranceRepository",
    "InsuranceIssueRequest",
    "InsuranceIssueResponse",
    "InsurancePolicyRow",
    "InsuranceRepositoryProtocol",
    "LoanNotApprovedForInsuranceError",
    "MotorInsuranceService",
    "PostgresInsuranceRepository",
    "RepositoryConfigurationError",
    "RepositoryError",
    "app",
    "create_app",
    "create_repository_from_env",
]
