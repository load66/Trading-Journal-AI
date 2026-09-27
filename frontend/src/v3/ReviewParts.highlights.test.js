import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';

import { Coaching } from './ReviewParts';


test('Coaching highlights positive and negative diagnosis phrases', () => {
  const summary = {
    narrative: 'QCOM showed patient execution, while WMT was averaged down into weakness.',
    mental_game: 'Discipline faded late in the session.',
    strengths: ['QCOM setup was executed well.'],
    mistakes: ['WMT was averaged down.'],
    coaching: ['Keep risk fixed on adds.'],
    patterns: [],
    behavior_flags: [],
    recorded_observations: [],
    highlights: {
      good: ['QCOM showed patient execution'],
      bad: [
        'WMT was averaged down into weakness',
        'Discipline faded late in the session',
      ],
    },
    ai_provider: 'groq',
    ai_model: 'openai/gpt-oss-120b',
  };

  const { container } = render(
    <Coaching
      summary={summary}
      loading={false}
      error=""
      onRetry={() => {}}
      onRegenerate={() => {}}
    />,
  );

  expect(screen.getByText('QCOM showed patient execution')).toHaveClass('coach-highlight', 'good');
  expect(screen.getByText('WMT was averaged down into weakness')).toHaveClass('coach-highlight', 'bad');
  expect(screen.getByText('Discipline faded late in the session')).toHaveClass('coach-highlight', 'bad');

  const strength = screen.getByText('QCOM setup was executed well.').closest('li');
  expect(strength).toHaveClass('good');

  fireEvent.click(screen.getByRole('tab', { name: 'Mistakes' }));
  const mistake = screen.getByText('WMT was averaged down.').closest('li');
  expect(mistake).toHaveClass('bad');

  expect(container.querySelectorAll('mark.coach-highlight.good')).toHaveLength(1);
  expect(container.querySelectorAll('mark.coach-highlight.bad')).toHaveLength(2);
});
