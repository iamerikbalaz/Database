import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { OpenRecordFolderButton } from './OpenRecordFolderButton';
import { materialLocalClient } from '../api/materialLocalClient';
import { directoryClient } from '../api/directoryClient';

afterEach(() => vi.restoreAllMocks());
it.each(['material', 'order'] as const)('opens the %s folder without selecting its row', async kind => {
  const open = vi.spyOn(kind === 'material' ? materialLocalClient : directoryClient, 'openFolder').mockResolvedValue();
  const select = vi.fn();
  render(<div onClick={select}><OpenRecordFolderButton kind={kind} id="record" name="SAMPLE" folderPath="folder" /></div>);
  fireEvent.click(screen.getByRole('button', { name: 'Open folder for SAMPLE' }));
  await waitFor(() => expect(open).toHaveBeenCalledWith('record'));
  expect(select).not.toHaveBeenCalled();
});
it('disables missing folder references and busy rows', () => {
  const { rerender } = render(<OpenRecordFolderButton kind="material" id="record" name="SAMPLE" folderPath={null} />);
  expect(screen.getByRole('button')).toBeDisabled();
  expect(screen.getByRole('button')).toHaveAttribute('title', 'No data folder assigned');
  rerender(<OpenRecordFolderButton kind="material" id="record" name="SAMPLE" folderPath="folder" disabled />);
  expect(screen.getByRole('button')).toBeDisabled();
});
it('keeps an unavailable folder retryable and reports the problem', async () => {
  vi.spyOn(materialLocalClient, 'openFolder').mockRejectedValue(new Error('PRIVATE'));
  render(<OpenRecordFolderButton kind="material" id="record" name="SAMPLE" folderPath="folder" />);
  fireEvent.click(screen.getByRole('button'));
  expect(await screen.findByRole('alert')).toHaveTextContent('Folder could not be opened');
  expect(screen.getByRole('alert')).not.toHaveTextContent('PRIVATE');
  expect(screen.getByRole('button')).not.toBeDisabled();
});
