

    function iobCalc(treatment, time) {

        var dia=profile.dia;
        if (dia == 3) {
            var peak=75;
        } else {
            console.warn('DIA of ' + dia + 'not supported');
        }
        var sens=profile.sens;
        if (typeof time === 'undefined') {
            var time = new Date();
        }

        if (treatment.insulin) {
            var bolusTime=new Date(treatment.date);
            var minAgo=(time-bolusTime)/1000/60;

            if (minAgo < 0) { 
                var iobContrib=0;
                var activityContrib=0;
            }
            if (minAgo < peak) {
                var x = minAgo/5+1;
                var iobContrib=treatment.insulin*(1-0.001852*x*x+0.001852*x);
                var activityContrib=sens*treatment.insulin*(2/dia/60/peak)*minAgo;

            }
            else if (minAgo < 180) {
                var x = (minAgo-75)/5;
                var iobContrib=treatment.insulin*(0.001323*x*x - .054233*x + .55556);
                var activityContrib=sens*treatment.insulin*(2/dia/60-(minAgo-peak)*2/dia/60/(60*dia-peak));
            }
            else {
                var iobContrib=0;
                var activityContrib=0;
            }
            return {
                iobContrib: iobContrib,
                activityContrib: activityContrib
            };
        }
        else {
            return '';
        }
    }
    function iobTotal(treatments, time) {
        var iob= 0;
        var activity = 0;
        if (!treatments) return {};
        if (typeof time === 'undefined') {
            var time = new Date();
        }

        treatments.forEach(function(treatment) {
            if(treatment.date < time.getTime( )) {
                var tIOB = iobCalc(treatment, time);
                if (tIOB && tIOB.iobContrib) iob += tIOB.iobContrib;
                if (tIOB && tIOB.activityContrib) activity += tIOB.activityContrib;
            }
        });

        return {
            iob: iob,
            activity: activity
        };
    }

    function calcTempTreatments() {
        var tempHistory = [];
        var tempBoluses = [];
        for (var i=0; i < pumpHistory.length; i++) {
            var current = pumpHistory[i];
            //if(pumpHistory[i].date < time) {
                if (pumpHistory[i]._type == "Bolus") {
                    var temp = {};
                    temp.timestamp = current.timestamp;
                    temp.started_at = new Date(current.date);
                    temp.started_at = new Date(current.date);
                    temp.date = current.date
                    temp.insulin = current.amount
                    tempBoluses.push(temp);
                } else if (pumpHistory[i]._type == "TempBasal") {
                    if (current.temp == 'percent') {
                      continue;
                    }
                    var rate = pumpHistory[i].rate;
                    var date = pumpHistory[i].date;
                    if (i>0 && pumpHistory[i-1].date == date && pumpHistory[i-1]._type == "TempBasalDuration") {
                        var duration = pumpHistory[i-1]['duration (min)'];
                    } else if (i+1<pumpHistory.length && pumpHistory[i+1].date == date && pumpHistory[i+1]._type == "TempBasalDuration") {
                        var duration = pumpHistory[i+1]['duration (min)'];
                    } else { console.log("No duration found for "+rate+" U/hr basal"+date); }
                    var temp = {};
                    temp.rate = rate;
                    temp.date = date;
                    temp.timestamp = current.timestamp;
                    temp.started_at = new Date(temp.date);
                    temp.duration = duration;
                    tempHistory.push(temp);
                }
            //}
        };
        for (var i=0; i+1 < tempHistory.length; i++) {
            if (tempHistory[i].date + tempHistory[i].duration*60*1000 > tempHistory[i+1].date) {
                tempHistory[i].duration = (tempHistory[i+1].date - tempHistory[i].date)/60/1000;
            }
        }
        var tempBolusSize;
        for (var i=0; i < tempHistory.length; i++) {
            if (tempHistory[i].duration > 0) {
                var netBasalRate = tempHistory[i].rate-profile.basal;
                if (netBasalRate < 0) { tempBolusSize = -0.1; }
                else { tempBolusSize = 0.1; }
                var netBasalAmount = Math.round(netBasalRate*tempHistory[i].duration*10/6)/100
                var tempBolusCount = Math.round(netBasalAmount / tempBolusSize);
                var tempBolusSpacing = tempHistory[i].duration / tempBolusCount;
                for (var j=0; j < tempBolusCount; j++) {
                    var tempBolus = {};
                    tempBolus.insulin = tempBolusSize;
                    tempBolus.date = tempHistory[i].date + j * tempBolusSpacing*60*1000;
                    tempBolus.created_at = new Date(tempBolus.date);
                    tempBoluses.push(tempBolus);
                }
            }
        }
        return [ ].concat(tempBoluses).concat(tempHistory);
        return {
            tempBoluses: tempBoluses,
            tempHistory: tempHistory
        };

    }

if (!module.parent) {
  var input = process.argv.slice(2,3).pop( )
  if (!input) {
    console.log('usage: ', process.argv.slice(0, 2), '<filename>');
    process.exit(1);
  }
  var all_data = require('./' + input);
  var pumpHistory = all_data;
  pumpHistory.reverse( );
  var profile = {
    basal: 0.9333333333333334
    ,carbratio: 13
    ,carbs_hr: 30
    ,dia: 3
    ,max_bg: 140
    ,max_iob: 4
    ,min_bg: 120
    ,sens: 45
    ,target_bg: 120
    ,type: "current"
  };
  var all_treatments =  calcTempTreatments( );
  var treatments = all_treatments; // .tempBoluses.concat(all_treatments.tempHistory);
  treatments.sort(function (a, b) { return a.timestamp > b.timestamp });
  var lastTimestamp = new Date(treatments[treatments.length -1].date + 1000 * 60);
  var iobs = iobTotal(treatments, lastTimestamp);
  // var iobs = iobTotal(pumpHistory.concat(treatments.tempBoluses), new Date(Date.parse(pumpHistory[0].timestamp) + 1000 * 60));
  // console.log(iobs);
  console.log(JSON.stringify(iobs));
}

