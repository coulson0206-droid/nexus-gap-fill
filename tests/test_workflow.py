# SPDX-License-Identifier: MIT
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import NexusGapFill_Single as gap
import NexusBatch as batch
import stage_session as staging
from test_gap_fill import FakeNexus, moving_clusters


class QueueSDK(FakeNexus):
    """Runs no Vicon algorithms. Simulates fills/errors; exports real synthetic BTK."""
    def __init__(self,directory,private):
        super().__init__(directory)
        self.trial='demo'
        self.private=private
        self.events=[]
        self.fail_native=False
        self.close_check=None

    def GetServerInfo(self): return 'Nexus',2,16,0,'Release'
    def GetTrialName(self): return self.folder,self.trial
    def SetTrialRegionOfInterest(self,first,last): self.roi=first,last
    def OpenTrial(self,path,timeout):
        self.events.append(('open',Path(path).name))
        clean=FakeNexus(Path(path).parent)
        self.data,self.truth,self.original=clean.data,clean.truth,clean.original
        self.trial=Path(path).name
    def CloseTrial(self,timeout):
        if self.close_check: self.close_check()
        self.events.append(('close',self.trial))

    def RunPipeline(self,name,location,timeout):
        root=ET.fromstring((self.private/(name+'.Pipeline')).read_bytes())
        if len(root)!=1 or root[0].get('OperationName')=='Python':
            raise RuntimeError('Nested/restarted Python operation in external queue')
        op=root[0].get('OperationName')
        self.events.append(('native',op))
        if self.fail_native:
            print('Host application failed to respond; outcome unknown',file=sys.stderr)
            return
        if op=='FillGapsWoltring':
            marker=self.markers[0]
            self.data[marker][0][10:13]=self.truth[10:13,0]
            self.data[marker][1][10:13]=True
            self.data[self.markers[1]][0][5]+=100  # must be restored
            self.data[self.markers[1]][0][100]+=100  # out-of-ROI must be restored
        if op=='ComputeSubjectMotion':
            self.data['optional_marker']=[np.ones((120,3)),np.ones(120,bool)]
        if op=='Exportc3d':
            from pyCGM2 import btk
            first,last=self.roi
            count=last-first+1
            a=btk.btkAcquisition();a.Init(len(self.markers),count,0,0)
            a.SetFirstFrame(first);a.SetPointFrequency(120.)
            for index,name in enumerate(self.markers):
                point=a.GetPoint(index);point.SetLabel('S:'+name)
                xyz,valid=self.data[name]
                point.SetValues(xyz[first-501:last-500])
                point.SetResiduals(np.where(valid[first-501:last-500],0.,-1.).reshape(-1,1))
            writer=btk.btkAcquisitionFileWriter();writer.SetInput(a)
            writer.SetFilename(str(Path(self.folder)/(self.trial+'._NGF.c3d')));writer.Update()


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='ngf_workflow_')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.source=self.root/'source';self.source.mkdir()
        for name in ('demo','second'):
            (self.source/(name+'.c3d')).write_bytes(('immutable_'+name).encode())
            (self.source/(name+'.Trial.enf')).write_text('[TRIAL_INFO]\nSUBJECTS=S\n')
        (self.source/'S.vsk').write_bytes(b'synthetic_saved_calibration')
        self.session=self.root/'copy'
        staging.stage(self.source,self.session,True,trials=['demo','second'])
        self.jobs=[dict(trial='demo',roi=[506,550]),dict(trial='second',roi=[506,550])]
        self.plan_path=self.root/'queue.json'
        gap.write_json(self.plan_path,batch.plan(self.session,'S',self.jobs,True))
        self.appdata=self.root/'appdata'
        self.private=self.appdata/'Vicon/Nexus2.x/Configurations/Pipelines'
        self.sdk=QueueSDK(self.session,self.private)
        self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ,APPDATA=str(self.appdata)))
        self.stack.enter_context(patch.object(gap,'ROOT',self.root/'cache'))
        self.output=io.StringIO()
        self.stack.enter_context(contextlib.redirect_stdout(self.output))

    def test_selective_copy_excludes_unrequested_trials_and_prior_exports(self):
        (self.source/'unrelated.x2d').write_bytes(b'x'*4096)
        (self.source/'unrelated.digitaldevices.xml').write_bytes(b'private_other_trial')
        (self.source/'demo._NGF.c3d').write_bytes(b'old_export')
        (self.source/'cgm_settings.xml').write_bytes(b'shared_model_settings')
        result=staging.stage(self.source,self.root/'selected',True,trials=['demo'])
        names=set(result['sha256'])
        self.assertIn('demo.c3d',names);self.assertIn('S.vsk',names)
        self.assertIn('cgm_settings.xml',names)
        self.assertNotIn('second.c3d',names);self.assertNotIn('unrelated.x2d',names)
        self.assertNotIn('unrelated.digitaldevices.xml',names);self.assertNotIn('demo._NGF.c3d',names)

    def test_raw_selected_trial_can_be_staged_but_is_not_silently_queued(self):
        (self.source/'raw.x2d').write_bytes(b'raw_2d')
        (self.source/'raw.Trial.enf').write_text('[TRIAL_INFO]')
        copied=self.root/'rawcopy'
        result=staging.stage(self.source,copied,True,trials=['raw'])
        self.assertEqual(result['status'],'verified_copy')
        with self.assertRaisesRegex(ValueError,'READY_C3D_REQUIRED'):
            batch.plan(copied,'S',[dict(trial='raw',roi=[1,10])])

    def test_missing_selected_trial_stops_before_creating_destination(self):
        target=self.root/'missing'
        with self.assertRaisesRegex(RuntimeError,'needs its ENF'):
            staging.stage(self.source,target,True,trials=['missing'])
        self.assertFalse(target.exists())

    def test_already_staged_source_is_not_repeatedly_recopied(self):
        with self.assertRaisesRegex(RuntimeError,'already contains'):
            staging.stage(self.session,self.root/'copy_again',True,trials=['demo'])

    def test_unchanged_columns_are_not_sent_to_sdk_including_nan_samples(self):
        xyz=np.ones((5,47,3));valid=np.ones((5,47),bool)
        xyz[0,0]=np.nan;valid[0,0]=False
        wanted=xyz.copy();wanted[2,3]=[3,4,5]
        calls=[]
        class Sink:
            def SetTrajectory(self,*args):calls.append(args[1])
        names=['point_%02d'%i for i in range(47)]
        written=gap._write(Sink(),dict(subject='S',description=dict(target_markers=names)),wanted,valid,xyz,valid)
        self.assertEqual(written,[names[3]])
        self.assertEqual(calls,[names[3]])

    def test_validity_only_changes_are_written(self):
        xyz=np.zeros((4,2,3));valid=np.zeros((4,2),bool);after=valid.copy();after[1,1]=True
        calls=[]
        class Sink:
            def SetTrajectory(self,*args):calls.append(args[1])
        gap._write(Sink(),dict(subject='S',description=dict(target_markers=['a','b'])),xyz,after,xyz,valid)
        self.assertEqual(calls,['b'])

    def test_two_trial_queue_has_no_host_python_operations_and_protects_sources(self):
        def check_previous_verified():
            journal=gap.read_json(self.plan_path.with_name('queue.state.json'))
            self.assertEqual(journal['jobs'][0]['outcome'],'verified')
        self.sdk.close_check=check_previous_verified
        csv_calls=[]
        original=gap._csv_report
        def count_csv(path,meta,result):
            csv_calls.append(meta['trial']);return original(path,meta,result)
        with patch.object(gap,'_csv_report',side_effect=count_csv):
            runner=batch.BatchRunner(self.plan_path,self.sdk)
            result=runner.run(setup_confirmed=True)
        self.assertEqual(result['status'],'complete')
        self.assertEqual([r['outcome'] for r in result['jobs']],['verified','verified'])
        self.assertEqual(csv_calls,['demo','second'])
        self.assertEqual(runner.nexus.counts.get('OpenTrial'),1)
        self.assertEqual(runner.nexus.counts.get('CloseTrial'),1)
        self.assertNotIn('SaveTrial',runner.nexus.counts)
        self.assertEqual(sum(r['summary']['fallback_added'] for r in result['jobs']),10)
        for row in result['jobs']:
            self.assertTrue(row['timings_seconds'])
            export=Path(row['export']['file']);self.assertTrue(export.exists())
            csv_path=self.session/(row['trial']+'_NGF.provenance.csv')
            self.assertEqual(csv_path.read_bytes(),(Path(row['run_path'])/'per_frame_provenance.csv').read_bytes())
            self.assertEqual((self.session/(row['trial']+'.c3d')).read_bytes(),('immutable_'+row['trial']).encode())
            self.assertEqual((self.source/(row['trial']+'.c3d')).read_bytes(),('immutable_'+row['trial']).encode())
        self.assertEqual((self.session/'S.vsk').read_bytes(),b'synthetic_saved_calibration')
        for name,(xyz,valid) in self.sdk.original.items():
            np.testing.assert_array_equal(self.sdk.data[name][0][valid],xyz[valid])

    def test_timeout_leaves_partial_trial_for_review_no_retry_or_next_open(self):
        self.sdk.fail_native=True
        runner=batch.BatchRunner(self.plan_path,self.sdk)
        result=runner.run(setup_confirmed=True)
        self.assertEqual(result['status'],'needs_attention')
        self.assertEqual(result['jobs'][0]['host_outcome'],'unknown_do_not_retry')
        self.assertEqual(result['jobs'][1]['outcome'],'pending')
        self.assertNotIn('OpenTrial',runner.nexus.counts)
        self.assertNotIn('CloseTrial',runner.nexus.counts)
        before=dict(runner.nexus.counts)
        with self.assertRaisesRegex(RuntimeError,'INTERRUPTED_JOB'):
            runner.run(setup_confirmed=True,resume=True)
        self.assertEqual(before,runner.nexus.counts)

    def test_modified_input_rejected_before_sdk_or_output(self):
        (self.session/'S.vsk').write_bytes(b'changed_calibration')
        runner=batch.BatchRunner(self.plan_path,self.sdk)
        with self.assertRaisesRegex(ValueError,'Inputs changed'):
            runner.run(setup_confirmed=True)
        self.assertEqual(runner.nexus.counts,{})
        self.assertFalse(runner.journal_path.exists())

    def test_wrong_current_trial_stops_without_changing_roi(self):
        self.sdk.trial='other'
        runner=batch.BatchRunner(self.plan_path,self.sdk)
        with self.assertRaisesRegex(RuntimeError,'SETUP_REQUIRED'):
            runner.run(setup_confirmed=True)
        self.assertFalse(runner.journal_path.exists())
        self.assertFalse((self.private.parent/'NGF_BATCH.lock').exists())
        self.assertNotIn('SetTrialRegionOfInterest',runner.nexus.counts)
        self.assertNotIn('RunPipeline',runner.nexus.counts)

    def test_setup_confirmation_required_once_before_mutation(self):
        runner=batch.BatchRunner(self.plan_path,self.sdk)
        with self.assertRaisesRegex(ValueError,'SETUP_REQUIRED'):
            runner.run()
        self.assertEqual(runner.nexus.counts,{})

    def test_verified_queue_resume_skips_all_sdk_work_and_detects_changed_export(self):
        result=batch.BatchRunner(self.plan_path,self.sdk).run(setup_confirmed=True)
        offline=batch.BatchRunner(self.plan_path)
        repeat=offline.run(setup_confirmed=True,resume=True)
        self.assertEqual(repeat['status'],'complete')
        self.assertIsNone(offline.nexus)
        Path(result['jobs'][0]['export']['file']).write_bytes(b'changed_output')
        with self.assertRaisesRegex(RuntimeError,'verified export changed'):
            batch.BatchRunner(self.plan_path).run(setup_confirmed=True,resume=True)

    def test_uncertain_queue_lock_prevents_a_second_queue(self):
        self.sdk.fail_native=True
        batch.BatchRunner(self.plan_path,self.sdk).run(setup_confirmed=True)
        other=self.root/'another_queue.json';other.write_bytes(self.plan_path.read_bytes())
        before=sum(event[0]=='native' for event in self.sdk.events)
        with self.assertRaisesRegex(RuntimeError,'QUEUE_LOCKED'):
            batch.BatchRunner(other,self.sdk).run(setup_confirmed=True)
        self.assertEqual(before,sum(event[0]=='native' for event in self.sdk.events))

    def test_calibration_change_stops_before_closing_verified_trial_or_opening_next(self):
        original=gap.verify_export
        def change_after_first_export(nexus,profile):
            original(nexus,profile)
            (self.session/'S.vsk').write_bytes(b'another_calibration')
        runner=batch.BatchRunner(self.plan_path,self.sdk)
        with patch.object(gap,'verify_export',side_effect=change_after_first_export):
            result=runner.run(setup_confirmed=True)
        self.assertEqual(result['jobs'][0]['outcome'],'verified')
        self.assertEqual(result['jobs'][1]['outcome'],'needs_attention')
        self.assertNotIn('CloseTrial',runner.nexus.counts)
        self.assertNotIn('OpenTrial',runner.nexus.counts)


if __name__=='__main__': unittest.main()
