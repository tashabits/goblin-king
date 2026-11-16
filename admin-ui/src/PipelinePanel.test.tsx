import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { PipelinePanel } from './PipelinePanel';

afterEach(cleanup);

it('requires a current discovery preview before attachment', async () => {
  const api = vi.fn(async (path: string) => path.endsWith('/discover') ? { routes: ['test', 'prod'] } : { enabled: true });
  const user = userEvent.setup();
  render(<PipelinePanel api={api} />);
  expect(screen.queryByRole('button', { name: 'Attach reviewed configuration' })).not.toBeInTheDocument();
  await user.click(screen.getByText('Attach or update a repository'));
  await user.click(screen.getByRole('button', { name: 'Discover contract and routes' }));
  expect(screen.getByRole('button', { name: 'Attach reviewed configuration' })).toBeEnabled();
  await user.type(screen.getByLabelText('Repository configuration'), ' ');
  expect(screen.queryByRole('button', { name: 'Attach reviewed configuration' })).not.toBeInTheDocument();
  expect(api).toHaveBeenCalledTimes(1);
});

it('sends recovery through the versioned authenticated API', async () => {
  const api = vi.fn(async (path: string, options?: RequestInit): Promise<any> => {
    if (options?.method === 'POST') return { operation_id: 'op-one' };
    if (path === '/pipeline/status') return { enabled: true, simulated_provider: true };
    if (path.startsWith('/pipeline/operations?')) return { items: [{ id: 'op-one', version: 7, status: 'held', document: { job_id: 'old-job' } }] };
    return { items: [] };
  });
  const user = userEvent.setup();
  render(<PipelinePanel api={api} />);
  await user.type(screen.getByLabelText('Project'), 'fixture');
  await user.click(screen.getByRole('button', { name: 'Inspect delivery' }));
  expect(screen.getByText(/review evidence is synthetic/)).toBeInTheDocument();
  await user.click(screen.getByText('operations (1)'));
  await user.click(screen.getByRole('button', { name: 'Verify termination and reconcile release' }));
  const mutation = api.mock.calls.find(([path]) => path === '/pipeline/operations/op-one/recover');
  expect(mutation).toBeDefined();
  expect(JSON.parse(mutation![1]!.body as string)).toMatchObject({ expected_version: 7, idempotency_key: expect.any(String) });
  expect(screen.getByRole('status')).toHaveTextContent('Operation op-one');
});
