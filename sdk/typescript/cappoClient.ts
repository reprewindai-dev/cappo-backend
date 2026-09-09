/** Veklom CAPPO TypeScript client.
 *
 * PROVISIONAL CONTRACT-ALIGNED STUB.
 *
 * This file is not yet described as mechanically generated from OpenAPI because the
 * current Notebook contract draft does not fully define requestBody/security schemas
 * for all operations. Never manufacture or expose Veklom-Authority headers.
 */

export class CappoError extends Error {
  constructor(public errorClass: string, message: string, public statusCode: number) {
    super(`[${errorClass}] (HTTP ${statusCode}) ${message}`);
    this.name = 'CappoError';
  }
}

export class AuthorityDeniedError extends CappoError {}
export class ExecutionIdMismatchError extends CappoError {}
export class EnvelopeSubstitutionError extends CappoError {}
export class ReplayDeniedError extends CappoError {}
export class RetryLockedError extends CappoError {}
export class AuthorityLockedError extends CappoError {}
export class InfrastructureUnavailableError extends CappoError {}

export interface EnvelopeSpec {
  memory_max_bytes: number;
  compute_fuel_units: number;
  wall_deadline_ms: number;
  allow_network: boolean;
}

export interface MountResponse {
  mount_id: string;
  lease_id: string;
  execution_id: string;
  envelope_digest: string;
}

export interface DispatchResponse {
  dispatch_id: string;
  execution_id: string;
  status: 'DISPATCHED' | 'COMPLETED' | 'OUTCOME_UNKNOWN';
  wal_sequence_id: number;
}

export interface ReconciliationResponse {
  execution_id: string;
  status: 'RECONCILED_SUCCEEDED' | 'RECONCILED_FAILED' | 'RECONCILIATION_UNAVAILABLE';
  finality_state: string;
}

export class CappoClient {
  private baseUrl: string;

  constructor(baseUrl = 'http://127.0.0.1:8002', private bearerToken?: string) {
    this.baseUrl = baseUrl.replace(/\/$/, '');
  }

  private headers(): Record<string, string> {
    const headers: Record<string, string> = {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    };
    if (this.bearerToken) headers.Authorization = `Bearer ${this.bearerToken}`;
    return headers;
  }

  private async handleErrorResponse(res: Response): Promise<never> {
    let errorClass = 'UnknownError';
    let message = await res.text();
    try {
      const data = JSON.parse(message);
      errorClass = data.error_class || errorClass;
      message = data.message || message;
    } catch {}

    const status = res.status;
    if (status === 401 || status === 403) throw new AuthorityDeniedError(errorClass, message, status);
    if (status === 422) {
      if (errorClass === 'ExecutionIdMismatchError') throw new ExecutionIdMismatchError(errorClass, message, status);
      if (errorClass === 'EnvelopeSubstitutionError') throw new EnvelopeSubstitutionError(errorClass, message, status);
    }
    if (status === 423) {
      if (errorClass === 'ReplayDeniedError') throw new ReplayDeniedError(errorClass, message, status);
      if (errorClass === 'RetryLockedError') throw new RetryLockedError(errorClass, message, status);
      if (errorClass === 'AuthorityLockedError') throw new AuthorityLockedError(errorClass, message, status);
    }
    if (status === 503) throw new InfrastructureUnavailableError(errorClass, message, status);
    throw new CappoError(errorClass, message, status);
  }

  private async post<T>(path: string, body: unknown): Promise<T> {
    const res = await fetch(`${this.baseUrl}${path}`, {
      method: 'POST',
      headers: this.headers(),
      body: JSON.stringify(body),
    });
    if (!res.ok) await this.handleErrorResponse(res);
    return (await res.json()) as T;
  }

  mountAuthority(capabilityId: string, envelopeSpec: EnvelopeSpec): Promise<MountResponse> {
    return this.post('/v1/mounts', { capability_id: capabilityId, envelope_spec: envelopeSpec });
  }

  dispatchConsequence(leaseId: string, executionId: string, envelopeDigest: string, actionPayload: Record<string, unknown>): Promise<DispatchResponse> {
    return this.post('/v1/consequence/dispatch', {
      lease_id: leaseId,
      execution_id: executionId,
      envelope_digest: envelopeDigest,
      action_payload: actionPayload,
    });
  }

  reconcileConsequence(executionId: string): Promise<ReconciliationResponse> {
    return this.post('/v1/consequence/reconcile', { execution_id: executionId });
  }
}
