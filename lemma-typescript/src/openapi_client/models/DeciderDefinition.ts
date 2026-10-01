/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ChoiceQuestion } from './ChoiceQuestion.js';
import type { InputView } from './InputView.js';
import type { MultiChoiceQuestion } from './MultiChoiceQuestion.js';
import type { Policy } from './Policy.js';
import type { Rule } from './Rule.js';
import type { ScaleQuestion } from './ScaleQuestion.js';
import type { YesNoQuestion } from './YesNoQuestion.js';
/**
 * What a decider version is: every field an engine or a rule reads.
 */
export type DeciderDefinition = {
    description: string;
    guidance?: (string | null);
    input?: InputView;
    policy?: Policy;
    questions: Record<string, (ChoiceQuestion | MultiChoiceQuestion | YesNoQuestion | ScaleQuestion)>;
    rules?: Array<Rule>;
};
