"""Allowlisted HTTP progress only; never record payloads, keys or raw exceptions."""
from copy import deepcopy
import re
import time
from uuid import uuid4

from services.understanding.geometry import InputError


class ModelCallError(InputError):
    def __init__(self, code, message, diagnostics):
        super().__init__(code, message)
        self.diagnostics = diagnostics


class TransportAudit:
    def __init__(self, callback=None, *, injected=False):
        self.started = time.monotonic()
        self.callback = callback
        self.model_request = False
        self.value = {
            'attempt_id': uuid4().hex, 'transport_kind': 'INJECTED' if injected else 'NETWORK',
            'stage': 'PREFLIGHT', 'http_request_started': False,
            'request_body_sent': False, 'response_headers_received': False,
            'response_body_complete': False, 'model_response_validated': False,
            'http_status': None, 'events': [],
        }
        self.update('PREFLIGHT')

    def snapshot(self):
        return deepcopy(self.value)

    def update(self, stage, **fields):
        self.value.update(stage=stage, elapsed_seconds=round(time.monotonic() - self.started, 3), **fields)
        if len(self.value['events']) < 40:
            self.value['events'].append({'stage': stage, 'seconds': self.value['elapsed_seconds']})
        if self.callback:
            self.callback(self.snapshot())

    def trace(self, name, info):
        # Proxy CONNECT has its own HTTP events: it is not a model POST.
        operation, _, state = name.rpartition('.')
        if operation.endswith('send_request_headers') and state == 'started':
            self.model_request = getattr(info.get('request'), 'method', None) in {b'POST', 'POST'}
        if operation.startswith(('http11.', 'http2.')) and not self.model_request:
            return
        phases = {
            'connect_tcp': 'CONNECTING', 'start_tls': 'TLS_HANDSHAKE',
            'send_request_headers': 'SENDING_HEADERS', 'send_request_body': 'SENDING_BODY',
            'receive_response_headers': 'WAITING_RESPONSE_HEADERS',
            'receive_response_body': 'READING_RESPONSE_BODY',
        }
        stage = phases.get(operation.rsplit('.', 1)[-1])
        if stage and state == 'started':
            self.update(stage)
        elif operation.endswith('send_request_body') and state == 'complete':
            # Local socket write completed; this is not proof of provider processing.
            self.update('REQUEST_BODY_SENT', request_body_sent=True)

    def response(self, response, secret):
        fields = {'response_headers_received': True, 'http_status': response.status_code}
        for name in ('x-request-id', 'request-id'):
            identifier = safe_identifier(response.headers.get(name), secret)
            if identifier:
                fields['response_request_id'] = identifier
                break
        self.update('RESPONSE_HEADERS_RECEIVED', **fields)

    def error(self, code, message, *, error_type=None):
        self.update('FAILED', failed_stage=self.value['stage'], error_type=error_type, error_code=code)
        return ModelCallError(code, message, self.snapshot())


def safe_identifier(value, secret):
    if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9._:-]{1,200}', value) and secret not in value:
        return value
    return None
