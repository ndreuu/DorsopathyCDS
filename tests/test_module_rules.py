"""Real source-bound referral predicates, unknown/conflict handling, frozen exports."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from dorsopathy_modules.complaints.screening import ScreeningComplaintsProvider, BOOLEAN_ANSWERS
from dorsopathy_platform.contracts import ContractError
from dorsopathy_platform.ports import ProviderContext
from dorsopathy_platform.storage import ArtifactStore
from dorsopathy_platform.api_contracts import canonical_bytes
from dorsopathy_cds.module_rules import evaluate_modules, load_module_catalog
from dorsopathy_cds.episode_report import evaluate_snapshot, render_html, ReportError
from test_snapshot_adapter import make_snapshot, make_manifest, make_mri, make_clinical, EPISODE

EXAMPLE=Path(__file__).resolve().parents[1]/'examples/complaints_screening/source.json'


class ModuleRuleTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.store=ArtifactStore(Path(self.directory.name))
        self.source=json.loads(EXAMPLE.read_text())

    def result(self,source=None,job='screen-one'):
        source=self.source if source is None else source
        ref=self.store.add_bytes(canonical_bytes(source),'application/json')
        request={'schema_version':'1.0','job_id':job,'episode_id':EPISODE,
                 'module_id':'complaints','module_version':'0.2.0','input_artifacts':[ref],
                 'parameters':{},'protocol_id':None}
        return ScreeningComplaintsProvider().analyze(request,ProviderContext(self.store))

    def check(self,results):
        return evaluate_modules(make_snapshot(results),make_manifest(results),self.store)

    def test_positive_patient_answers_generate_referral_action_with_exact_citations(self):
        result=self.result();report=self.check([result]);check=report['results'][0]
        self.assertEqual(check['status'],'met');self.assertEqual(check['action'],'immediate_local_assessment')
        self.assertEqual(check['diagnostic_output'],'screening_action_only')
        self.assertFalse(report['disease_exclusion']);self.assertIsNone(report['diagnostic_probability'])
        self.assertEqual(len(check['leaves']),6)
        docs=load_module_catalog()['sources']['documents']
        self.assertTrue(all(q['page']==18 for q in docs[0]['quotes']))
        for concept in BOOLEAN_ANSWERS.values():
            accepted=report['lineage'][concept]['accepted']
            self.assertEqual(accepted['source_sha256'],result['artifacts'][0]['sha256'])

    def test_missing_new_symptoms_do_not_become_false_or_exclude_disease(self):
        for key in list(BOOLEAN_ANSWERS)[2:]:self.source['answers'][key]={'assessment_status':'not_assessed','value':None,'unit':None}
        check=self.check([self.result()])['results'][0]
        self.assertEqual(check['status'],'indeterminate');self.assertEqual(len(check['decisive_missing_fact_ids']),4)
        self.assertIsNone(check['action'])
        self.assertTrue(all(leaf['observed'] is None for leaf in check['leaves'][2:]))

    def test_numerical_pain_score_does_not_invent_severe_threshold(self):
        self.source['answers']['severe_low_back_pain']={'assessment_status':'not_assessed','value':None,'unit':None}
        self.source['answers']['pain_intensity']['value']=10
        self.assertEqual(self.check([self.result()])['results'][0]['status'],'indeterminate')

    def test_negative_rule_is_not_disease_exclusion(self):
        self.source['answers']['severe_low_back_pain']['value']=False
        report=self.check([self.result()]);check=report['results'][0]
        self.assertEqual(check['status'],'not_met');self.assertIn('не исключены',check['interpretation'])
        self.assertFalse(report['disease_exclusion'])

    def test_invalid_boolean_and_unknown_nonnull_inputs_rejected(self):
        for update in ({'value':1},{'value':'yes'},{'assessment_status':'not_assessed'},{'unit':'1'}):
            source=deepcopy(self.source);source['answers']['leg_radiation'].update(update)
            with self.subTest(update=update),self.assertRaises(ContractError):self.result(source)

    def test_forged_observation_is_not_accepted_even_when_rule_would_be_positive(self):
        self.source['answers']['severe_low_back_pain']['value']=False
        result=self.result();obs=next(o for o in result['observations'] if o['concept']=='symptoms.low_back_pain.severe')
        obs['value']=True
        report=self.check([result]);self.assertEqual(report['results'][0]['status'],'indeterminate')
        self.assertIn('questionnaire_observation_source_mismatch',report['lineage'][obs['concept']]['reason_codes'])

    def test_model_predictions_cannot_replace_patient_answers(self):
        result=self.result();result['observations'][2]['origin']='model_predicted'
        result['observations'][2]['model']={'id':'unrelated-model','version':'1','weights_sha256':'1'*64}
        self.assertEqual(self.check([result])['results'][0]['status'],'indeterminate')

    def test_multiple_forms_need_separate_selection_and_mixed_origins_block(self):
        one=self.result();two=self.result(job='screen-two')
        self.assertEqual(self.check([one,two])['results'][0]['status'],'indeterminate')
        real=make_mri(self.store,origin='automatically_measured')
        self.assertEqual(self.check([one,real])['results'][0]['status'],'indeterminate')

    def test_real_questionnaire_can_drive_local_rule_without_sensor_linkage(self):
        self.source.update(mode='patient_input',origin='patient_report')
        result=self.result();mri=make_mri(self.store,origin='automatically_measured')
        report=evaluate_modules(make_snapshot([result,mri]),make_manifest([result,mri],mode='live',linkage='unknown'),self.store)
        self.assertEqual(report['results'][0]['status'],'met')
        self.assertEqual(report['results'][0]['scope'],'questionnaire_only')

    def test_processing_mode_cannot_relabel_real_patient_answers_as_demo(self):
        self.source.update(mode='patient_input',origin='patient_report')
        report=self.check([self.result()])
        self.assertEqual(report['results'][0]['status'],'indeterminate')
        self.assertIn('screening_mode_origin_mismatch',report['lineage']['symptoms.low_back_pain.severe']['reason_codes'])

    def test_new_report_and_legacy_saved_report_render_without_current_catalog(self):
        result=self.result();report=evaluate_snapshot(make_snapshot([result]),make_manifest([result]),self.store)
        self.assertEqual(report['schema_version'],'2.0');self.assertIn('Refer immediately',render_html(report))
        legacy=deepcopy(report);legacy.pop('module_report');legacy['schema_version']='1.0'
        content={k:v for k,v in legacy.items() if k not in {'report_id','content_sha256'}}
        legacy['content_sha256']=hashlib.sha256(canonical_bytes(content)).hexdigest();legacy['report_id']='report-'+legacy['content_sha256']
        self.assertIn('ДПКР',render_html(legacy))
        report['module_report']['catalog_snapshot']['rules'][0]['version']='changed'
        with self.assertRaises(ReportError):render_html(report)

    def test_module_use_binds_compact_facts_to_the_exact_immutable_result(self):
        mri=make_mri(self.store);report=self.check([mri]);use=report['module_use'][0]
        self.assertEqual(use['result_sha256'],hashlib.sha256(canonical_bytes(mri)).hexdigest())
        self.assertEqual(use['observations'][0]['value'],mri['observations'][0]['value'])
        self.assertEqual(use['evidence_artifacts'],mri['artifacts'])
        self.assertNotIn('evidence_refs',use['observations'][0])

    def test_reviewed_pathology_result_is_accepted_and_not_performed_conflicts(self):
        mri=make_mri(self.store);mri['module_id']='mri.pathology'
        exam=make_clinical(self.store,mri)
        report=evaluate_snapshot(make_snapshot([mri,exam]),make_manifest([mri,exam]),self.store)
        self.assertEqual(report['clinical_report']['results'][0]['status'],'met')
        exam=make_clinical(self.store,study='not_performed')
        report=evaluate_snapshot(make_snapshot([mri,exam]),make_manifest([mri,exam]),self.store)
        self.assertEqual(report['clinical_report']['results'][0]['status'],'indeterminate')


if __name__=='__main__':unittest.main()
