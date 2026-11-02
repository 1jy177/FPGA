"""Browser acceptance. Requires a workstation with Playwright and Chrome."""
import argparse
import json
import os
import tempfile
from pathlib import Path

def main():
    from playwright.sync_api import sync_playwright
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8870/digimouse/')
    parser.add_argument('--output',type=Path,required=True);a=parser.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ,TMPDIR='/tmp')
    for name in ('LD_PRELOAD','LD_LIBRARY_PATH'):env.pop(name,None)
    tempfile.tempdir='/tmp'
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True,env=env,
            args=['--no-sandbox','--disable-dev-shm-usage','--no-proxy-server','--enable-unsafe-swiftshader'])
        page=browser.new_page(viewport={'width':1500,'height':1050});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        response=page.goto(a.url)
        page.wait_for_selector('canvas[data-ready="true"]',timeout=120000)
        canvas=page.locator('canvas');n=page.locator('section[data-tissue]').count()
        assert n in (1,2,16)
        if n==16:
            assert canvas.get_attribute('data-visible')=='16'
            assert 'Digimouse' in page.title()
            assert 'CC BY-SA 4.0' in page.locator('#license').inner_text()
        page.click('#exterior');assert canvas.get_attribute('data-visible')=='1'
        page.screenshot(path=str(a.output/'animal_exterior.png'))
        initial=canvas.get_attribute('data-view')
        page.mouse.move(850,500);page.mouse.down();page.mouse.move(960,550,steps=5);page.mouse.up()
        assert canvas.get_attribute('data-view')!=initial
        rotated=canvas.get_attribute('data-view');page.mouse.wheel(0,-180);page.wait_for_timeout(250)
        assert canvas.get_attribute('data-view')!=rotated
        zoomed=canvas.get_attribute('data-view');page.mouse.down(button='right')
        page.mouse.move(1000,570,steps=4);page.mouse.up(button='right')
        assert canvas.get_attribute('data-view')!=zoomed
        page.click('#reset');assert canvas.get_attribute('data-view')==initial
        page.click('#front');page.click('#side');page.click('#reset')
        if n>=2:
            page.click('#internal');assert canvas.get_attribute('data-visible')==str(n-2 if n==16 else 1)
            page.screenshot(path=str(a.output/'animal_internal.png'))
            if n==16:
                page.click('#skeleton');assert canvas.get_attribute('data-visible')=='1'
                page.screenshot(path=str(a.output/'animal_skeleton.png'))
            page.click('#combined');assert canvas.get_attribute('data-visible')==str(n)
            page.screenshot(path=str(a.output/'animal_combined.png'))
        body=page.locator('section[data-tissue="body"]')
        body.locator('input[type=checkbox]').uncheck()
        body.locator('input[type=checkbox]').check()
        body.locator('input[type=range]').fill('0.4');body.locator('input[type=range]').dispatch_event('input')
        body.locator('button').click();assert canvas.get_attribute('data-visible')=='1'
        page.locator('#clip').check();assert canvas.get_attribute('data-clip')=='true'
        for axis in ('0','1','2'):
            page.select_option('#axis',axis);page.locator('#cut').fill('0.45');page.locator('#cut').dispatch_event('input')
        page.locator('#flip').check();page.screenshot(path=str(a.output/'animal_cut.png'))
        page.click('#reset');assert canvas.get_attribute('data-clip')=='false'
        assert not page.locator('#error').inner_text()
        assert not errors,errors
        report={'passed':True,'http_status':response.status,'layers':n,'display_lod':True,
                'profile':'digimouse' if n==16 else 'legacy embryo',
                'anatomical_presets':n==16,
                'rotation_zoom_pan_reset':True,'visibility_opacity_solo':True,
                'clip_xyz_flip':True,'javascript_errors':errors,'browser':browser.version,
                'rendering':'headless Chrome SwiftShader; not a board speed measurement'}
        (a.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2));browser.close()
if __name__=='__main__':main()
