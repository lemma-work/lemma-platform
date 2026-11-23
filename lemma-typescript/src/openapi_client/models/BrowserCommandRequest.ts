/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { HistoryCommand } from './HistoryCommand.js';
import type { NavigateCommand } from './NavigateCommand.js';
import type { TabCommand } from './TabCommand.js';
import type { TabsCommand } from './TabsCommand.js';
export type BrowserCommandRequest = {
    action: (NavigateCommand | HistoryCommand | TabsCommand | TabCommand);
};
