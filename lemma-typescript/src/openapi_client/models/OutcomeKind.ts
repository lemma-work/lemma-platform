/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The two shapes of what a pod owns.
 *
 * ``week`` is a bar held every week, which never finishes ("Every customer
 * hears back within a day"). ``date`` is a finish line, reached once by its
 * ``due`` date ("The launch ships by the date it is due"). The values are the plan
 * file's own (``kind: week`` in ``/plan.md``), so a plan a person or the
 * pod's agent wrote reads straight across.
 */
export enum OutcomeKind {
    WEEK = 'week',
    DATE = 'date',
}
